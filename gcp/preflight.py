#!/usr/bin/env python3
"""Kubespace read-only pre-flight for Google Cloud.

Runs under your own gcloud identity and only reads. It collects, for the scope you name:

- the organization, folders and projects;
- GKE clusters: private or public, Autopilot or Standard, DNS endpoint, release channel;
- organization policies, including iam.allowedPolicyMemberDomains and iam.managed.allowedPolicyMembers;
- VPC Service Controls perimeters and which of your projects are in them;
- which of the APIs Kubespace reads are enabled.

It prints the exact JSON it would send, then asks before sending it to Kubespace with your
single-use pairing code. It never sends credentials or tokens. Anything you can't read is
reported as unknown, with the reason, never as absent.

Standard library only. Needs python3 and gcloud on PATH.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

SCHEMA = "kubespace.gcp-preflight/v1"
# The APIs Kubespace's read-only connection calls. Only these are reported, nothing else you have enabled.
KUBESPACE_APIS = (
    "artifactregistry.googleapis.com", "binaryauthorization.googleapis.com", "cloudasset.googleapis.com",
    "cloudbuild.googleapis.com", "clouddeploy.googleapis.com", "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com", "container.googleapis.com", "gkehub.googleapis.com", "orgpolicy.googleapis.com",
    "secretmanager.googleapis.com", "serviceusage.googleapis.com",
)
DRS_CONSTRAINTS = ("iam.allowedPolicyMemberDomains", "iam.managed.allowedPolicyMembers")
MAX_FOLDERS = 500
MAX_PROJECTS = 1000
CODE = re.compile(r"ksp_[A-Z2-7]{26}\Z")
SCOPE = re.compile(r"(organizations|folders)/([0-9]{1,20})\Z|projects/([a-z][a-z0-9-]{4,28}[a-z0-9])\Z")
ENDPOINT_HOST = re.compile(r"([a-z0-9-]+\.)*kubespace\.ai\Z")
# Never sent, whatever a field holds: access tokens, JWTs, private keys, API keys, key file fields.
SECRET = re.compile(r"ya29\.|eyJ[A-Za-z0-9_-]{10,}\.|-----BEGIN|AIza[0-9A-Za-z_-]{20}|private_key|client_secret|"
                    r"refresh_token|access_token|1//0[A-Za-z0-9_-]{20}", re.IGNORECASE)
_DISPLAY = re.compile(r"[^\x00-\x1f\x7f]{1,120}\Z")
_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
_SERVICE = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.googleapis\.com\Z")
_POLICY_VALUE = re.compile(r"[A-Za-z0-9:/._@*-]{1,200}\Z")
_PERIMETER = re.compile(r"accessPolicies/[0-9]{1,20}/servicePerimeters/[A-Za-z0-9_]{1,50}\Z")
_CHANNELS = ("RAPID", "REGULAR", "STABLE", "EXTENDED")

Runner = Callable[[list[str]], tuple[int, str, str]]


class Unreadable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def classify(stderr: str) -> str:
    """Why gcloud could not read something, as one of the reasons Kubespace accepts."""
    text = stderr.lower()
    if "vpc_service_controls" in text or "vpc service controls" in text or "prohibited by organization's policy" in text:
        return "vpc_sc_blocked"
    if "service_disabled" in text or "has not been used in project" in text or "it is disabled" in text:
        return "api_not_enabled"
    if "permission_denied" in text or "does not have" in text or "forbidden" in text or "403" in text:
        return "permission_denied"
    if "not_found" in text or "not found" in text or "404" in text:
        return "not_found"
    return "error"


def gcloud(args: list[str]) -> tuple[int, str, str]:
    done = subprocess.run(["gcloud", *args, "--format=json", "--quiet"], capture_output=True, text=True, check=False)
    return done.returncode, done.stdout, done.stderr


class Reader:
    def __init__(self, run: Runner) -> None:
        self.run = run

    def json(self, *args: str) -> Any:
        code, out, err = self.run(list(args))
        if code != 0:
            raise Unreadable(classify(err))
        try:
            return json.loads(out or "null")
        except ValueError:
            raise Unreadable("error") from None


def _display(value: Any) -> str | None:
    return value if isinstance(value, str) and _DISPLAY.fullmatch(value) and not SECRET.search(value) else None


def _section(read: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"status": "read", **read()}  # read() may set status "partial" with its reason
    except Unreadable as refused:
        return {"status": "unknown", "reason": refused.reason}


def _scope_flag(kind: str, ident: str) -> str:
    return {"organizations": "--organization", "folders": "--folder", "projects": "--project"}[kind] + "=" + ident


def organization_of(reader: Reader, kind: str, ident: str) -> dict[str, Any]:
    if kind == "organizations":
        org_id = ident
    else:
        ancestors = reader.json("projects", "get-ancestors", ident) if kind == "projects" else \
            reader.json("resource-manager", "folders", "get-ancestors", ident)
        orgs = [a.get("id") for a in ancestors or [] if isinstance(a, dict) and a.get("type") == "organization"]
        if not orgs:
            raise Unreadable("not_found")
        org_id = str(orgs[0])
    found = {"id": org_id}
    try:
        name = _display((reader.json("organizations", "describe", org_id) or {}).get("displayName"))
        if name:
            found["display_name"] = name
    except Unreadable:
        pass  # The id is known; the name is optional.
    return found


def folders_under(reader: Reader, kind: str, ident: str) -> dict[str, Any]:
    """Every folder under the scope, breadth first. A folder scope includes itself."""
    items: list[dict[str, Any]] = []
    queue: list[tuple[str, str]] = [(kind, ident)]
    if kind == "folders":
        root = reader.json("resource-manager", "folders", "describe", ident) or {}
        parent = str(root.get("parent", ""))
        if re.fullmatch(r"(organizations|folders)/[0-9]{1,20}", parent):
            items.append({"id": ident, "parent": parent, **(
                {"display_name": d} if (d := _display(root.get("displayName"))) else {})})
    status: dict[str, Any] = {}
    while queue and len(items) < MAX_FOLDERS:
        parent_kind, parent = queue.pop(0)
        flag = "--organization" if parent_kind == "organizations" else "--folder"
        try:
            children = reader.json("resource-manager", "folders", "list", f"{flag}={parent}") or []
        except Unreadable as refused:
            status = {"status": "partial", "reason": refused.reason}
            continue
        for child in children:
            folder_id = str(child.get("name", "")).removeprefix("folders/")
            if not folder_id.isdigit():
                continue
            items.append({"id": folder_id, "parent": f"{parent_kind}/{parent}", **(
                {"display_name": d} if (d := _display(child.get("displayName"))) else {})})
            queue.append(("folders", folder_id))
    out: dict[str, Any] = {"items": items[:MAX_FOLDERS]}
    if queue or len(items) > MAX_FOLDERS:
        out["truncated"] = True
    out.update(status)
    return out


def projects_under(reader: Reader, kind: str, ident: str, folders: list[dict[str, Any]]) -> dict[str, Any]:
    if kind == "projects":
        project = reader.json("projects", "describe", ident) or {}
        parent = project.get("parent") or {}
        item = {"project_id": ident, "project_number": str(project.get("projectNumber", "")) or None}
        if parent.get("type") in ("organization", "folder") and str(parent.get("id", "")).isdigit():
            item["parent"] = f"{parent['type']}s/{parent['id']}"
        return {"items": [{k: v for k, v in item.items() if v}]}
    parents = [(kind.rstrip("s"), ident)] + [("folder", f["id"]) for f in folders if f["id"] != ident]
    items: list[dict[str, Any]] = []
    status: dict[str, Any] = {}
    for parent_type, parent_id in parents:
        if len(items) >= MAX_PROJECTS:
            status["truncated"] = True
            break
        try:
            found = reader.json("projects", "list",
                                f"--filter=parent.type={parent_type} AND parent.id={parent_id} AND lifecycleState=ACTIVE")
        except Unreadable as refused:
            status.update({"status": "partial", "reason": refused.reason})
            continue
        for project in found or []:
            project_id = str(project.get("projectId", ""))
            if _PROJECT.fullmatch(project_id):
                items.append({"project_id": project_id, "project_number": str(project.get("projectNumber", "")),
                              "parent": f"{parent_type}s/{parent_id}"})
    return {"items": items[:MAX_PROJECTS], **status}


def _cluster(project_id: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    name, location = str(raw.get("name", "")), str(raw.get("location", ""))
    if not re.fullmatch(r"[a-z]([-a-z0-9]{0,38}[a-z0-9])?", name) or not re.fullmatch(r"[a-z]+(-[a-z0-9]+){1,3}", location):
        return None
    private_cfg = raw.get("privateClusterConfig") or {}
    network = raw.get("networkConfig") or {}
    private = private_cfg.get("enablePrivateNodes")
    if private is None:
        private = network.get("defaultEnablePrivateNodes")
    dns = ((raw.get("controlPlaneEndpointsConfig") or {}).get("dnsEndpointConfig") or {})
    channel = (raw.get("releaseChannel") or {}).get("channel")
    return {"project_id": project_id, "name": name, "location": location,
            "private": bool(private) if private is not None else False,
            "autopilot": bool((raw.get("autopilot") or {}).get("enabled", False)),
            "dns_endpoint": bool(dns.get("allowExternalTraffic")) if dns else False,
            "release_channel": channel if channel in _CHANNELS else "UNSPECIFIED"}


def per_project(reader: Reader, projects: list[str], read: Callable[[Reader, str], Any],
                workers: int = 8) -> tuple[list[Any], list[dict[str, str]]]:
    def one(project_id: str) -> tuple[str, Any, str | None]:
        try:
            return project_id, read(reader, project_id), None
        except Unreadable as refused:
            return project_id, None, refused.reason
    found: list[Any] = []
    unreadable: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for project_id, value, reason in pool.map(one, projects):
            if reason is None:
                found.append(value)
            else:
                unreadable.append({"project_id": project_id, "reason": reason})
    return found, unreadable


def _with_unreadable(items: list[Any], unreadable: list[dict[str, str]]) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "read", "items": items, "unreadable": unreadable}
    if unreadable:  # Some projects were not readable: the section is partial, never complete.
        reasons = {u["reason"] for u in unreadable}
        out.update({"status": "partial", "reason": reasons.pop() if len(reasons) == 1 else "error"})
    return out


def clusters(reader: Reader, projects: list[str]) -> dict[str, Any]:
    def read(r: Reader, project_id: str) -> list[dict[str, Any]]:
        return [c for raw in r.json("container", "clusters", "list", f"--project={project_id}") or []
                if (c := _cluster(project_id, raw)) is not None]
    found, unreadable = per_project(reader, projects, read)
    return _with_unreadable([c for group in found for c in group], unreadable)


def enabled_apis(reader: Reader, projects: list[str]) -> dict[str, Any]:
    def read(r: Reader, project_id: str) -> dict[str, Any]:
        names = {str((s.get("config") or {}).get("name", "")) for s in
                 r.json("services", "list", "--enabled", f"--project={project_id}") or []}
        return {"project_id": project_id, "enabled": sorted(names & set(KUBESPACE_APIS))}
    found, unreadable = per_project(reader, projects, read)
    return _with_unreadable(found, unreadable)


def _policy_item(constraint: str, resource: str, policy: dict[str, Any]) -> dict[str, Any]:
    spec = policy.get("spec") or {}
    rules = spec.get("rules") or []
    enforced: bool | None = None
    values: list[str] = []
    for rule in rules:
        if rule.get("allowAll"):
            enforced = False if enforced is None else enforced
        if rule.get("denyAll") or rule.get("values") or rule.get("enforce") is True:
            enforced = True
        if rule.get("enforce") is False and enforced is None:
            enforced = False
        values += [v for v in (rule.get("values") or {}).get("allowedValues", []) if isinstance(v, str)]
        params = rule.get("parameters") or {}
        for key in ("allowedMemberSubjects", "allowedPrincipalSets"):
            values += [v for v in params.get(key, []) if isinstance(v, str)]
    item: dict[str, Any] = {"constraint": constraint, "resource": resource, "enforced": enforced}
    kept = [v for v in values if _POLICY_VALUE.fullmatch(v) and not SECRET.search(v)][:50]
    if kept:
        item["allowed_values"] = kept
    return item


def org_policies(reader: Reader, kind: str, ident: str, org_id: str | None) -> dict[str, Any]:
    """The effective DRS constraints at the scope, and every policy set on the scope and its organization."""
    resource = f"{kind}/{ident}"
    items: list[dict[str, Any]] = []
    refusals: list[str] = []
    for constraint in DRS_CONSTRAINTS:
        try:
            policy = reader.json("org-policies", "describe", constraint, "--effective", _scope_flag(kind, ident))
        except Unreadable as refused:
            if refused.reason != "not_found":  # not_found: set nowhere above the scope, so nothing is enforced.
                refusals.append(refused.reason)
            continue
        items.append(_policy_item(constraint, resource, policy or {}))
    seen = {(i["constraint"], i["resource"]) for i in items}
    owners = [(kind, ident)] + ([("organizations", org_id)] if org_id and kind != "organizations" else [])
    for owner_kind, owner in owners:
        try:
            listed = reader.json("org-policies", "list", _scope_flag(owner_kind, owner)) or []
        except Unreadable as refused:
            refusals.append(refused.reason)
            continue
        for policy in listed:
            constraint = str(policy.get("name", "")).rsplit("/policies/", 1)[-1]
            key = (constraint, f"{owner_kind}/{owner}")
            if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9.]{2,120}", constraint) and key not in seen:
                seen.add(key)
                items.append(_policy_item(constraint, key[1], policy))
    if refusals and not items:
        raise Unreadable(refusals[0])
    if refusals:
        return {"items": items[:500], "status": "partial", "reason": refusals[0]}
    return {"items": items[:500]}


def perimeters(reader: Reader, org_id: str | None, project_numbers: dict[str, str]) -> dict[str, Any]:
    """Perimeters that hold at least one project in scope, naming only those projects."""
    if not org_id:
        raise Unreadable("not_found")
    items: list[dict[str, Any]] = []
    for policy in reader.json("access-context-manager", "policies", "list", f"--organization={org_id}") or []:
        policy_id = str(policy.get("name", "")).removeprefix("accessPolicies/")
        if not policy_id.isdigit():
            continue
        for perimeter in reader.json("access-context-manager", "perimeters", "list", f"--policy={policy_id}") or []:
            name = str(perimeter.get("name", ""))
            if not _PERIMETER.fullmatch(name):
                continue
            configs = [(False, perimeter.get("status") or {})]
            if perimeter.get("useExplicitDryRunSpec") and perimeter.get("spec"):
                configs.append((True, perimeter["spec"]))
            for dry_run, config in configs:
                inside = sorted(r for r in config.get("resources", []) if r in project_numbers)
                if not inside:
                    continue
                item = {"name": name, "dry_run": dry_run, "projects": inside,
                        "restricted_services": sorted(s for s in config.get("restrictedServices", [])
                                                      if isinstance(s, str) and _SERVICE.fullmatch(s))}
                if title := _display(perimeter.get("title")):
                    item["title"] = title
                items.append(item)
    return {"items": items[:100]}


def collect(scope: str, run: Runner = gcloud, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    match = SCOPE.fullmatch(scope)
    if not match:
        raise ValueError("scope must be organizations/<id>, folders/<id> or projects/<project-id>")
    kind = match.group(1) or "projects"
    ident = match.group(2) or match.group(3)
    reader = Reader(run)
    organization = _section(lambda: organization_of(reader, kind, ident))
    folders = {"status": "read", "items": []} if kind == "projects" else _section(lambda: folders_under(reader, kind, ident))
    projects = _section(lambda: projects_under(reader, kind, ident, folders.get("items", [])))
    project_ids = [p["project_id"] for p in projects.get("items", [])]
    numbers = {f"projects/{p['project_number']}": p["project_id"] for p in projects.get("items", [])
               if p.get("project_number")}
    org_id = organization.get("id")
    stamp = (now or (lambda: datetime.now(timezone.utc)))().strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = {
        "schema": SCHEMA,
        "generated_at": stamp,
        "scope": {"type": {"organizations": "organization", "folders": "folder", "projects": "project"}[kind],
                  "id": ident},
        "organization": organization,
        "folders": folders,
        "projects": projects,
        "gke_clusters": clusters(reader, project_ids) if projects["status"] != "unknown" else
        {"status": "unknown", "reason": projects["reason"]},
        "org_policies": _section(lambda: org_policies(reader, kind, ident, org_id)),
        "vpc_sc": _section(lambda: perimeters(reader, org_id, numbers)),
        "enabled_apis": enabled_apis(reader, project_ids) if projects["status"] != "unknown" else
        {"status": "unknown", "reason": projects["reason"]},
    }
    return _clean(summary)


def _clean(summary: dict[str, Any]) -> dict[str, Any]:
    for section in summary.values():
        if isinstance(section, dict) and section.get("status") == "unknown":
            for key in ("items", "unreadable", "id", "display_name", "truncated"):
                section.pop(key, None)
        if isinstance(section, dict) and section.get("status") == "read":
            section.pop("reason", None)
    return summary


def strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def check_endpoint(endpoint: str) -> str:
    url = urllib.parse.urlsplit(endpoint)
    if url.scheme != "https" or not ENDPOINT_HOST.fullmatch(url.hostname or "") or url.query or url.username:
        raise ValueError("the endpoint must be an https://…kubespace.ai address, as shown in Kubespace")
    return endpoint


def post(endpoint: str, code: str, summary: dict[str, Any]) -> tuple[int, str]:
    body = json.dumps({"pairing_code": code, "summary": summary}).encode()
    request = urllib.request.Request(endpoint, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "kubespace-preflight/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310 - https only, checked above
            return answer.status, answer.read(2048).decode(errors="replace")
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read(2048).decode(errors="replace")


TTY = "/dev/tty"


def ask(prompt: str) -> str | None:
    """The answer typed at the terminal, or None when there is no terminal to ask.

    Unbuffered bytes: a text-mode "r+" handle on a terminal fails because a terminal can't seek.
    """
    try:
        with open(TTY, "rb+", buffering=0) as tty:
            tty.write(prompt.encode("utf-8"))
            return tty.readline().decode("utf-8", "replace").strip()
    except OSError:
        return None


def main(argv: list[str] | None = None, run: Runner = gcloud,
         send: Callable[[str, str, dict[str, Any]], tuple[int, str]] = post,
         answer: Callable[[str], str | None] = ask, out: Any = None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Kubespace read-only Google Cloud pre-flight.")
    parser.add_argument("--scope", required=True, help="organizations/<id>, folders/<id> or projects/<project-id>")
    parser.add_argument("--endpoint", help="the Kubespace address shown with your pairing code")
    parser.add_argument("--code", help="your single-use Kubespace pairing code")
    parser.add_argument("--dry-run", action="store_true", help="print the summary and send nothing")
    args = parser.parse_args(argv)
    if not args.dry_run:
        if not args.code or not CODE.fullmatch(args.code):
            parser.error("--code must be the pairing code Kubespace shows (ksp_ and 26 characters)")
        try:
            check_endpoint(args.endpoint or "")
        except ValueError as bad:
            parser.error(str(bad))
    try:
        summary = collect(args.scope, run)
    except ValueError as bad:
        parser.error(str(bad))
    if any(SECRET.search(text) for text in strings(summary)):
        print("Refusing: something in the summary looks like a credential. Nothing was sent.", file=out)
        return 2
    text = json.dumps(summary, indent=2, sort_keys=True)
    print("This is the exact JSON the pre-flight would send to Kubespace:\n", file=out)
    print(text, file=out)
    if args.dry_run:
        print("\nDry run: nothing was sent.", file=out)
        return 0
    reply = answer(f"\nSend this summary to {args.endpoint}? Type yes to send: ")
    if reply is None:
        print("\nNo terminal to ask on, so nothing was sent. Run it again in an interactive shell.", file=out)
        return 3
    if reply.lower() != "yes":
        print("Nothing was sent.", file=out)
        return 1
    status, body = send(args.endpoint, args.code, summary)
    if status == 200:
        print("Sent. Return to Kubespace: the agent now proposes the scope and names any blockers.", file=out)
        return 0
    detail = re.sub(r"[^A-Za-z0-9_ .:-]", "", body)[:200]
    print(f"Kubespace refused the summary (HTTP {status}): {detail}", file=out)
    print("A pairing code works once and for 15 minutes. Get a new one in Kubespace if it has expired.", file=out)
    return 4


if __name__ == "__main__":
    sys.exit(main())
