#!/usr/bin/env python3
"""Kubespace read-only pre-flight for Azure.

Runs under your own Azure CLI session (Azure Cloud Shell, or any shell after ``az login``) and only
reads. For the scope you name it collects:

- the directory (Microsoft Entra tenant);
- the management groups and the subscriptions under them;
- Azure Policy assignments whose deny effect would stop the setup: creating the user-assigned
  managed identity, its federated credential, the Reader role assignment or the deployment;
- which of the resource providers Kubespace reads through are not registered.

It prints the exact JSON it would send, then asks before sending it to Kubespace with your
single-use pairing code. It never sends credentials or tokens, and it never creates, assigns or
registers anything. Anything you can't read is reported as unknown, with the reason, never as absent.

Standard library only. Needs python3 and az on PATH.
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

SCHEMA = "kubespace.azure-preflight/v1"
# The resource providers Kubespace's read-only connection reads through. Only these are reported.
RESOURCE_PROVIDERS = ("Microsoft.ContainerService", "Microsoft.KubernetesConfiguration", "Microsoft.ManagedIdentity")
# What the setup creates, by Azure resource type: a deny on one of these stops it.
TARGET_TYPES = {
    "microsoft.managedidentity/userassignedidentities": "managed_identity",
    "microsoft.managedidentity/userassignedidentities/federatedidentitycredentials": "federated_credential",
    "microsoft.authorization/roleassignments": "role_assignment",
    "microsoft.resources/deployments": "deployment",
}
TARGETS = ("managed_identity", "federated_credential", "role_assignment", "deployment")
MAX_GROUPS = 500
MAX_SUBSCRIPTIONS = 1000
# Policy and provider reads are per subscription; past this many the sections say truncated.
MAX_READ_SUBSCRIPTIONS = 200
API = "https://management.azure.com"
CODE = re.compile(r"ksp_[A-Z2-7]{26}\Z")
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
GROUP = r"[A-Za-z0-9_().-]{1,90}"
SCOPE = re.compile(rf"directory\Z|managementGroups/({GROUP})\Z|subscriptions/({UUID})\Z")
ENDPOINT_HOST = re.compile(r"([a-z0-9-]+\.)*kubespace\.ai\Z")
# Never sent, whatever a field holds: access tokens, JWTs, private keys, key fields, storage keys, SAS.
SECRET = re.compile(r"ya29\.|eyJ[A-Za-z0-9_-]{10,}\.|-----BEGIN|AIza[0-9A-Za-z_-]{20}|private_key|client_secret|"
                    r"refresh_token|access_token|1//0[A-Za-z0-9_-]{20}|AccountKey=|SharedAccessSignature|[?&]sig=",
                    re.IGNORECASE)
_DISPLAY = re.compile(r"[^\x00-\x1f\x7f]{1,120}\Z")
_AT = rf"(/providers/Microsoft\.Management/managementGroups/{GROUP}|/subscriptions/{UUID}(/resourceGroups/{GROUP})?)"
_ASSIGNMENT = re.compile(rf"(?i){_AT}/providers/Microsoft\.Authorization/policyAssignments/[A-Za-z0-9_.-]{{1,128}}\Z")
_DEFINITION = re.compile(
    rf"(?i)({_AT})?/providers/Microsoft\.Authorization/(policyDefinitions|policySetDefinitions)/[A-Za-z0-9_.-]{{1,128}}\Z")
_ASSIGNMENT_SCOPE = re.compile(rf"(?i){_AT}\Z")
_PARAMETER = re.compile(r"\[parameters\('([^']{1,128})'\)\]\Z")
_STATES = ("Enabled", "Warned", "PastDue", "Disabled", "Deleted")

Runner = Callable[[list[str]], tuple[int, str, str]]


class Unreadable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def classify(stderr: str) -> str:
    """Why az could not read something, as one of the reasons Kubespace accepts."""
    text = stderr.lower()
    if "az login" in text or "not logged in" in text or "no subscription found" in text:
        return "not_signed_in"
    if "authorizationfailed" in text or "does not have authorization" in text or "forbidden" in text or "403" in text:
        return "permission_denied"
    if "notfound" in text or "not found" in text or "404" in text:
        return "not_found"
    return "error"


def az(args: list[str]) -> tuple[int, str, str]:
    done = subprocess.run(["az", *args, "--output", "json", "--only-show-errors"], capture_output=True, text=True,
                          check=False)
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

    def get(self, path: str, api_version: str, query: str = "") -> Any:
        """One ARM GET. ``az rest`` signs it with your session; the token never leaves az."""
        return self.json("rest", "--method", "get", "--url", f"{API}{path}?api-version={api_version}{query}")


def _display(value: Any) -> str | None:
    return value if isinstance(value, str) and _DISPLAY.fullmatch(value) and not SECRET.search(value) else None


def _uuid(value: Any) -> str | None:
    return value.lower() if isinstance(value, str) and re.fullmatch(UUID, value) else None


def _section(read: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"status": "read", **read()}  # read() may set status "partial" with its reason
    except Unreadable as refused:
        return {"status": "unknown", "reason": refused.reason}


# --- Directory, management groups, subscriptions -------------------------------------------------


def directory_of(reader: Reader) -> dict[str, Any]:
    account = reader.json("account", "show") or {}
    tenant = _uuid(account.get("tenantId"))
    if not tenant:
        raise Unreadable("not_signed_in")
    found: dict[str, Any] = {"id": tenant}
    try:
        for listed in (reader.get("/tenants", "2022-12-01") or {}).get("value", []):
            if _uuid(listed.get("tenantId")) == tenant and (name := _display(listed.get("displayName"))):
                found["display_name"] = name
    except Unreadable:
        pass  # The id is known; the name is optional.
    return found


def _walk(node: dict[str, Any], parent: str | None, groups: list[dict[str, Any]], subs: dict[str, str | None],
          names: dict[str, str]) -> None:
    # ARM puts the root's display name and children under properties; nested children carry them directly.
    node = {**node, **(node.get("properties") or {})}
    kind = str(node.get("type", "")).lower()
    name = str(node.get("name", ""))
    if kind.endswith("/subscriptions") or kind == "/subscriptions":
        if (sub := _uuid(name)) and len(subs) < MAX_SUBSCRIPTIONS:
            subs.setdefault(sub, parent)
            if display := _display(node.get("displayName")):
                names.setdefault(sub, display)
        return
    if not re.fullmatch(GROUP, name) or len(groups) >= MAX_GROUPS:
        return
    item: dict[str, Any] = {"id": name}
    if parent:
        item["parent"] = parent
    if display := _display(node.get("displayName")):
        item["display_name"] = display
    groups.append(item)
    for child in node.get("children") or []:
        if isinstance(child, dict):
            _walk(child, name, groups, subs, names)


def estate(reader: Reader, kind: str, ident: str, tenant: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The management groups and subscriptions in scope, as two sections."""
    accounts: dict[str, dict[str, Any]] = {}
    account_reason = None
    try:
        for account in reader.json("account", "list", "--all") or []:
            if (sub := _uuid(account.get("id"))) and (not tenant or _uuid(account.get("tenantId")) == tenant):
                accounts[sub] = account
    except Unreadable as refused:
        account_reason = refused.reason

    groups: list[dict[str, Any]] = []
    in_tree: dict[str, str | None] = {}
    names: dict[str, str] = {}
    group_section: dict[str, Any]
    if kind == "subscriptions":
        group_section = {"status": "read", "items": []}
    else:
        root = ident if kind == "managementGroups" else tenant
        try:
            if not root:
                raise Unreadable("not_signed_in")
            # A plain ARM GET. Not `az account management-group show`: that command registers the
            # Microsoft.Management resource provider in the current subscription, which is a write.
            tree = reader.get(f"/providers/Microsoft.Management/managementGroups/{root}", "2021-04-01",
                              "&$expand=children&$recurse=true") or {}
            _walk(tree, None, groups, in_tree, names)
            group_section = {"status": "read", "items": groups}
            if len(groups) >= MAX_GROUPS:
                group_section["truncated"] = True
        except Unreadable as refused:
            group_section = {"status": "unknown", "reason": refused.reason}

    if kind == "subscriptions":
        wanted = {ident.lower(): None}
    elif group_section["status"] == "read":
        wanted = in_tree
    elif kind == "directory" and account_reason is None:
        # No management-group read: the subscriptions you can see in this directory, without their group.
        wanted = {sub: None for sub in accounts}
    else:
        reason = group_section.get("reason") or account_reason or "error"
        return group_section, {"status": "unknown", "reason": reason}
    items = []
    for sub, group in list(wanted.items())[:MAX_SUBSCRIPTIONS]:
        account = accounts.get(sub, {})
        item: dict[str, Any] = {"subscription_id": sub}
        if account.get("state") in _STATES:
            item["state"] = account["state"]
        if group:
            item["management_group"] = group
        if display := _display(account.get("name")) or names.get(sub):
            item["display_name"] = display
        items.append(item)
    subs_section: dict[str, Any] = {"status": "read", "items": items}
    if len(wanted) > MAX_SUBSCRIPTIONS:
        subs_section["truncated"] = True
    if group_section["status"] == "unknown" and kind == "directory":
        subs_section.update({"status": "partial", "reason": group_section["reason"]})
    if kind == "subscriptions" and ident.lower() not in accounts and account_reason:
        subs_section.update({"status": "partial", "reason": account_reason})
    return group_section, subs_section


# --- Azure Policy denies -------------------------------------------------------------------------


def _resolve(value: Any, parameters: dict[str, Any]) -> Any:
    """A ``[parameters('x')]`` expression, resolved; anything else unchanged."""
    if isinstance(value, str) and (match := _PARAMETER.fullmatch(value.strip())):
        return parameters.get(match.group(1))
    return value


def _types(value: Any) -> set[str]:
    values = value if isinstance(value, list) else [value]
    return {v.lower() for v in values if isinstance(v, str)}


def _can(condition: Any, parameters: dict[str, Any]) -> tuple[set[str], set[str]]:
    """For a policy rule's ``if``: the setup resource types for which it can be true, and can be false.

    Only ``type`` conditions are evaluated; any other field may go either way, so it narrows nothing.
    That over-reports a deny conditional on, say, a location, which is the safe side for a warning.
    """
    every = set(TARGET_TYPES)
    if isinstance(condition, dict) and "allOf" in condition:
        parts = [_can(c, parameters) for c in condition["allOf"] or []]
        return (set.intersection(every, *(t for t, _ in parts)), set().union(*(f for _, f in parts)))
    if isinstance(condition, dict) and "anyOf" in condition:
        parts = [_can(c, parameters) for c in condition["anyOf"] or []]
        return (set().union(*(t for t, _ in parts)), set.intersection(every, *(f for _, f in parts)))
    if isinstance(condition, dict) and "not" in condition:
        true, false = _can(condition["not"], parameters)
        return false, true
    if not isinstance(condition, dict) or str(condition.get("field", "")).lower() != "type":
        return every, every
    for op, negated in (("equals", False), ("in", False), ("like", False), ("notEquals", True), ("notIn", True),
                        ("notLike", True)):
        if op not in condition:
            continue
        listed = _types(_resolve(condition[op], parameters))
        if "like" in op.lower():
            match = {t for t in every if any(re.fullmatch(re.escape(p).replace(r"\*", ".*"), t) for p in listed)}
        else:
            match = every & listed
        return (every - match, match) if negated else (match, every - match)
    return every, every


def _values(definition_params: Any, given: Any) -> dict[str, Any]:
    values = {k: v.get("defaultValue") for k, v in (definition_params or {}).items() if isinstance(v, dict)}
    values.update({k: v.get("value") for k, v in (given or {}).items() if isinstance(v, dict) and "value" in v})
    return values


def _deny_targets(definition: dict[str, Any], parameters: dict[str, Any]) -> set[str]:
    props = definition.get("properties") or {}
    rule = props.get("policyRule") or {}
    values = _values(props.get("parameters"), {k: {"value": v} for k, v in parameters.items()})
    effect = str(_resolve((rule.get("then") or {}).get("effect"), values) or "").lower()
    if effect != "deny":
        return set()
    hits = _can(rule.get("if"), values)[0]
    if str(props.get("mode", "All")).lower() == "indexed":
        # Indexed mode evaluates only types with tags and a location: the identity, not the
        # federated credential, the role assignment or the deployment.
        hits &= {"microsoft.managedidentity/userassignedidentities"}
    return {TARGET_TYPES[t] for t in hits}


class Definitions:
    """Policy definitions read once each, through ARM GETs."""

    def __init__(self, reader: Reader) -> None:
        self.reader = reader
        self.cache: dict[str, Any] = {}

    def read(self, definition_id: str) -> Any:
        key = definition_id.lower()
        if key not in self.cache:
            try:
                self.cache[key] = self.reader.get(definition_id, "2023-04-01") or {}
            except Unreadable as refused:
                self.cache[key] = refused
        found = self.cache[key]
        if isinstance(found, Unreadable):
            raise found
        return found

    def targets(self, definition_id: str, given: Any) -> set[str]:
        definition = self.read(definition_id)
        props = definition.get("properties") or {}
        values = _values(props.get("parameters"), given)
        if "/policysetdefinitions/" not in definition_id.lower():
            return _deny_targets(definition, values)
        found: set[str] = set()
        for member in props.get("policyDefinitions") or []:
            member_id = str(member.get("policyDefinitionId", ""))
            if not _DEFINITION.fullmatch(member_id):
                continue
            member_params = {k: _resolve(v.get("value"), values) for k, v in (member.get("parameters") or {}).items()
                             if isinstance(v, dict)}
            found |= _deny_targets(self.read(member_id), member_params)
        return found


def policy_denies(reader: Reader, kind: str, ident: str, subscriptions: list[str]) -> dict[str, Any]:
    """Assignments that apply in scope and would deny what the setup creates. Inherited ones included."""
    definitions = Definitions(reader)
    seen: dict[str, dict[str, Any]] = {}
    unreadable: list[dict[str, str]] = []
    refusals: list[str] = []

    def assignments(args: list[str]) -> list[dict[str, Any]]:
        return [a for a in reader.json("policy", "assignment", "list", *args) or [] if isinstance(a, dict)]

    listings: list[tuple[str | None, list[str]]] = []
    if kind == "managementGroups":
        listings.append((None, ["--scope", f"/providers/Microsoft.Management/managementGroups/{ident}"]))
    listings += [(sub, ["--subscription", sub, "--disable-scope-strict-match"])
                 for sub in subscriptions[:MAX_READ_SUBSCRIPTIONS]]

    def one(listing: tuple[str | None, list[str]]) -> tuple[str | None, list[dict[str, Any]] | None, str | None]:
        try:
            return listing[0], assignments(listing[1]), None
        except Unreadable as refused:
            return listing[0], None, refused.reason

    with ThreadPoolExecutor(max_workers=8) as pool:
        listed = list(pool.map(one, listings))
    for sub, found, reason in listed:
        if found is None:
            if sub:
                unreadable.append({"subscription_id": sub, "reason": reason or "error"})
            else:
                refusals.append(reason or "error")
            continue
        for assignment in found:
            assignment_id = str(assignment.get("id", ""))
            definition_id = str(assignment.get("policyDefinitionId", ""))
            scope = str(assignment.get("scope", ""))
            if assignment_id.lower() in seen or not (_ASSIGNMENT.fullmatch(assignment_id) and
                                                     _DEFINITION.fullmatch(definition_id) and
                                                     _ASSIGNMENT_SCOPE.fullmatch(scope)):
                continue
            if sub and any(str(n).lower() == f"/subscriptions/{sub}" for n in assignment.get("notScopes") or []):
                continue
            try:
                targets = definitions.targets(definition_id, assignment.get("parameters"))
            except Unreadable as refused:
                refusals.append(refused.reason)
                continue
            if targets:
                seen[assignment_id.lower()] = {
                    "assignment": assignment_id, "scope": scope, "definition": definition_id, "effect": "deny",
                    "enforced": str(assignment.get("enforcementMode", "Default")) != "DoNotEnforce",
                    "targets": [t for t in TARGETS if t in targets]}
    if listed and all(found is None for _, found, _ in listed):  # Nothing at all was readable.
        raise Unreadable(next(reason for _, _, reason in listed) or "error")
    out: dict[str, Any] = {"items": list(seen.values())[:500], "unreadable": unreadable}
    reasons = refusals + [u["reason"] for u in unreadable]
    if reasons:
        out.update({"status": "partial", "reason": reasons[0]})
    if len(subscriptions) > MAX_READ_SUBSCRIPTIONS:
        out["truncated"] = True
    return out


# --- Resource providers --------------------------------------------------------------------------


def resource_providers(reader: Reader, subscriptions: list[str]) -> dict[str, Any]:
    def one(sub: str) -> tuple[str, list[str] | None, str | None]:
        try:
            listed = reader.json("provider", "list", "--subscription", sub,
                                 "--query", "[].{namespace:namespace, state:registrationState}") or []
        except Unreadable as refused:
            return sub, None, refused.reason
        registered = {str(p.get("namespace", "")).lower() for p in listed
                      if isinstance(p, dict) and p.get("state") == "Registered"}
        return sub, [n for n in RESOURCE_PROVIDERS if n.lower() not in registered], None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, subscriptions[:MAX_READ_SUBSCRIPTIONS]))
    items = [{"subscription_id": sub, "unregistered": missing} for sub, missing, _ in results if missing is not None]
    unreadable = [{"subscription_id": sub, "reason": reason or "error"} for sub, missing, reason in results
                  if missing is None]
    if results and not items:
        raise Unreadable(unreadable[0]["reason"])
    out: dict[str, Any] = {"items": items, "unreadable": unreadable}
    if unreadable:
        reasons = {u["reason"] for u in unreadable}
        out.update({"status": "partial", "reason": reasons.pop() if len(reasons) == 1 else "error"})
    if len(subscriptions) > MAX_READ_SUBSCRIPTIONS:
        out["truncated"] = True
    return out


# --- The summary ---------------------------------------------------------------------------------


def collect(scope: str, run: Runner = az, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    match = SCOPE.fullmatch(scope)
    if not match:
        raise ValueError("scope must be directory, managementGroups/<id> or subscriptions/<subscription-id>")
    kind = "managementGroups" if match.group(1) else "subscriptions" if match.group(2) else "directory"
    ident = match.group(1) or (match.group(2) or "").lower()
    reader = Reader(run)
    directory = _section(lambda: directory_of(reader))
    tenant = directory.get("id")
    groups, subscriptions = estate(reader, kind, ident, tenant)
    readable = [s["subscription_id"] for s in subscriptions.get("items", []) if s.get("state") not in ("Disabled", "Deleted")]
    unknown = {"status": "unknown", "reason": subscriptions.get("reason", "error")}
    stamp = (now or (lambda: datetime.now(timezone.utc)))().strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind == "directory" and not tenant:
        raise SystemExit("Sign in first: az login (Azure Cloud Shell signs you in already).")
    scope_out = {"directory": {"type": "directory", "id": tenant},
                 "managementGroups": {"type": "management_group", "id": ident},
                 "subscriptions": {"type": "subscription", "id": ident}}[kind]
    summary = {
        "schema": SCHEMA,
        "generated_at": stamp,
        "scope": scope_out,
        "directory": directory,
        "management_groups": groups,
        "subscriptions": subscriptions,
        "policy_denies": _section(lambda: policy_denies(reader, kind, ident, readable))
        if subscriptions["status"] != "unknown" or kind == "managementGroups" else unknown,
        "resource_providers": _section(lambda: resource_providers(reader, readable))
        if subscriptions["status"] != "unknown" else unknown,
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
    """The answer typed at the terminal, or None when there is no terminal to ask (unbuffered, see gcp/)."""
    try:
        with open(TTY, "rb+", buffering=0) as tty:
            tty.write(prompt.encode("utf-8"))
            return tty.readline().decode("utf-8", "replace").strip()
    except OSError:
        return None


def main(argv: list[str] | None = None, run: Runner = az,
         send: Callable[[str, str, dict[str, Any]], tuple[int, str]] = post,
         answer: Callable[[str], str | None] = ask, out: Any = None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Kubespace read-only Azure pre-flight.")
    parser.add_argument("--scope", default="directory",
                        help="directory, managementGroups/<id> or subscriptions/<subscription-id>")
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
        print("Sent. Return to Kubespace: the setup card shows the summary and pre-fills the scope.", file=out)
        return 0
    detail = re.sub(r"[^A-Za-z0-9_ .:-]", "", body)[:200]
    print(f"Kubespace refused the summary (HTTP {status}): {detail}", file=out)
    print("A pairing code works once and for 15 minutes. Get a new one in Kubespace if it has expired.", file=out)
    return 4


if __name__ == "__main__":
    sys.exit(main())
