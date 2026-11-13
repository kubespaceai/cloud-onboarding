#!/usr/bin/env python3
"""Kubespace keyless read-only access for Google Cloud: apply the blueprint from your setup code.

Runs under your own gcloud identity. Kubespace receives nothing from it: no key, no token, no
summary. The blueprint code from the Kubespace setup card names your scope and the Workload
Identity pool; the roles, the APIs and the VPC Service Controls rule are the ones below, the same
objects as the Terraform module in ``terraform/gcp`` (a test pins the two together):

- the APIs the token exchange needs, enabled in project P that holds the pool;
- a Workload Identity pool in P and a provider that accepts only your Kubespace org's Google subject;
- a ``kubespace-reader`` service account in P, which only that subject may impersonate;
- Kubespace's read roles for the reader on the scope (project, folder or organization);
- the APIs Kubespace's read calls use, enabled in the quota project;
- optionally, with ``--vpc-sc-perimeter``, an ingress rule that admits the reader only, to the read
  services only.

It reads first and prints what is missing, asks, then creates only that and prints each change.
Nothing is ever removed or replaced (a pool or provider you revoked is undeleted), and running it again
changes nothing.

Standard library only. Needs python3 (3.9 or later) and gcloud on PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

BLUEPRINT = "kgb1"
# The read roles, as in terraform/gcp/main.tf: a folder or organization also gets roles/browser so
# Kubespace can list the projects under it.
READ_ROLES = (
    "roles/artifactregistry.reader",
    "roles/binaryauthorization.policyViewer",
    "roles/cloudasset.viewer",
    "roles/cloudbuild.builds.viewer",
    "roles/clouddeploy.viewer",
    "roles/compute.networkViewer",
    "roles/container.viewer",
    "roles/gkehub.viewer",
    "roles/orgpolicy.policyViewer",
    "roles/secretmanager.viewer",
    "roles/serviceusage.serviceUsageConsumer",
)
SCOPE_ROLES = ("roles/browser",)
# Enabled in the quota project, which Kubespace's read calls bill.
READ_APIS = (
    "artifactregistry.googleapis.com",
    "binaryauthorization.googleapis.com",
    "cloudasset.googleapis.com",
    "cloudbuild.googleapis.com",
    "clouddeploy.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "gkehub.googleapis.com",
    "orgpolicy.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
)
# Enabled in P for the token exchange and the impersonation.
POOL_APIS = (
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
)
# What the VPC Service Controls ingress rule admits the reader to.
VPC_SC_SERVICES = (
    "cloudasset.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "serviceusage.googleapis.com",
)
PROVIDER_ID = "kubespace-org"
READER_ACCOUNT_ID = "kubespace-reader"
ISSUER = "https://accounts.google.com"
MAPPING = {"google.subject": "assertion.sub"}
IMPERSONATION_ROLE = "roles/iam.workloadIdentityUser"

_PROJECT = r"[a-z][a-z0-9-]{4,28}[a-z0-9]"
_CODE = re.compile(rf"{BLUEPRINT}\.([pfo])\.([a-z0-9-]{{1,30}})\.(-|{_PROJECT})\.(-|{_PROJECT})\.([0-9]{{6,19}})\."
                   r"([a-z0-9-]{4,32})\.([0-9]{6,32})\.([0-9a-f]{6})\Z")
_PROJECT_ID = re.compile(_PROJECT + r"\Z")
_NUMERIC = re.compile(r"[0-9]{1,20}\Z")
_PERIMETER = re.compile(r"accessPolicies/([0-9]{1,20})/servicePerimeters/([A-Za-z0-9_]{1,50})\Z")
_KIND = {"p": "project", "f": "folder", "o": "organization"}

Runner = Callable[[list[str]], tuple[int, str, str]]


# --- The blueprint code ---------------------------------------------------------------------

def checksum(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()[:6]


def decode(code: str) -> dict[str, Any]:
    """The blueprint the code names. Raises ValueError for anything that is not a well-formed code.

    ``kgb1.<p|f|o>.<scope id>.<quota project|->.<pool project|->.<pool project number>.<pool id>.<subject>.<check>``:
    ``-`` for the quota project means the scope's project; for the pool project, the quota project.
    """
    match = _CODE.fullmatch(code.strip())
    if match is None:
        raise ValueError("this is not a Kubespace blueprint code: copy it again from the setup card")
    body, check = code.strip().rsplit(".", 1)
    if checksum(body) != check:
        raise ValueError("the blueprint code's checksum does not match: copy it again from the setup card")
    kind_letter, scope_id, quota, pool_project, number, pool_id, subject, _ = match.groups()
    kind = _KIND[kind_letter]
    if kind == "project":
        if not _PROJECT_ID.fullmatch(scope_id):
            raise ValueError("the code's project scope is not a project ID")
        quota = scope_id if quota == "-" else quota
    else:
        if not _NUMERIC.fullmatch(scope_id):
            raise ValueError(f"the code's {kind} scope is not a numeric ID")
        if quota == "-":
            raise ValueError("a folder or organization code names its quota project")
    pool_project = quota if pool_project == "-" else pool_project
    reader = f"{READER_ACCOUNT_ID}@{pool_project}.iam.gserviceaccount.com"
    member = (f"principal://iam.googleapis.com/projects/{number}/locations/global/"
              f"workloadIdentityPools/{pool_id}/subject/{subject}")
    return {
        "blueprint": BLUEPRINT,
        "scope": {"type": kind, "id": scope_id},
        "quota_project": quota,
        "pool": {"project_id": pool_project, "project_number": number, "pool_id": pool_id,
                 "provider_id": PROVIDER_ID, "issuer": ISSUER, "attribute_mapping": dict(MAPPING),
                 "attribute_condition": f"assertion.sub == '{subject}'"},
        "reader": reader,
        "impersonation_member": member,
        "roles": list(READ_ROLES if kind == "project" else READ_ROLES + SCOPE_ROLES),
        "apis": {"pool_project": list(POOL_APIS), "quota_project": list(READ_APIS)},
        "vpc_sc": {"identity": f"serviceAccount:{reader}", "services": list(VPC_SC_SERVICES)},
    }


def encode(scope_type: str, scope_id: str, quota: str, pool_project: str, number: str, pool_id: str,
           subject: str) -> str:
    """The code for a blueprint; Kubespace builds the same string (tests pin both sides)."""
    letter = {v: k for k, v in _KIND.items()}[scope_type]
    quota_part = "-" if scope_type == "project" and quota == scope_id else quota
    pool_part = "-" if pool_project == quota else pool_project
    body = ".".join((BLUEPRINT, letter, scope_id, quota_part, pool_part, number, pool_id, subject))
    return f"{body}.{checksum(body)}"


# --- gcloud ---------------------------------------------------------------------------------

class GcloudError(Exception):
    def __init__(self, args: list[str], reason: str, stderr: str, retried: bool = False) -> None:
        super().__init__(reason)
        self.args_ = args
        self.reason = reason
        self.stderr = stderr
        self.retried = retried  # The step was retried while IAM caught up, and still failed.


def classify(stderr: str) -> str:
    text = stderr.lower()
    if "already_exists" in text or "already exists" in text:
        return "already_exists"
    if "vpc_service_controls" in text or "vpc service controls" in text:
        return "vpc_sc_blocked"
    if "service_disabled" in text or "has not been used in project" in text or "it is disabled" in text:
        return "api_not_enabled"
    if "permission_denied" in text or "does not have" in text or "forbidden" in text or "403" in text:
        return "permission_denied"
    if "not_found" in text or "not found" in text or "does not exist" in text or "404" in text:
        return "not_found"
    return "error"


def gcloud(args: list[str]) -> tuple[int, str, str]:
    done = subprocess.run(["gcloud", *args, "--format=json", "--quiet"], capture_output=True, text=True, check=False)
    return done.returncode, done.stdout, done.stderr


class Cloud:
    def __init__(self, run: Runner) -> None:
        self.run = run

    def read(self, *args: str) -> Any:
        """The JSON gcloud prints, or None when the object does not exist (or its API is still off)."""
        code, out, err = self.run(list(args))
        if code != 0:
            reason = classify(err)
            if reason in ("not_found", "api_not_enabled"):
                return None
            raise GcloudError(list(args), reason, err)
        try:
            return json.loads(out or "null")
        except ValueError:
            raise GcloudError(list(args), "error", "gcloud printed something that is not JSON") from None


# --- The plan -------------------------------------------------------------------------------

@dataclass
class Change:
    what: str
    args: list[str]
    file: str | None = None  # Content for the one argument that names a file ({file}).
    after_account_create: bool = False  # Follows creating the service account: IAM takes a while to see it.
    # A deleted pool or provider is kept for 30 days and still owns its id, so a create answers ALREADY_EXISTS
    # (and a describe may answer NOT_FOUND). ``undelete`` is tried when the create says it exists; ``then`` runs
    # once it is back, to set it to the blueprint. ``undeleted`` names the object a restore brought back.
    undelete: list[str] | None = None
    then: list[list[str]] = field(default_factory=list)
    undeleted: str | None = None

    def command(self, path: str = "FILE") -> list[str]:
        return [a.replace("{file}", path) for a in self.args]


# Waits between tries of a step that follows creating the service account: a minute in all.
CATCH_UP_DELAYS = (5, 10, 15, 15, 15)
CATCH_UP_REASONS = ("not_found", "permission_denied")


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def _enabled(cloud: Cloud, project: str) -> set[str] | None:
    listed = cloud.read("services", "list", "--enabled", f"--project={project}")
    if listed is None:
        return None
    return {s.get("config", {}).get("name", "") for s in listed if isinstance(s, dict)}


def _plan_apis(cloud: Cloud, plan: Plan, project: str, apis: list[str], planned: set[tuple[str, str]]) -> None:
    """Each API once per project: P and the quota project are often the same project."""
    apis = [a for a in apis if (project, a) not in planned]
    if not apis:
        return
    planned.update((project, a) for a in apis)
    enabled = _enabled(cloud, project) or set()
    missing = [a for a in apis if a not in enabled]
    plan.unchanged.extend(f"API {a} in {project}" for a in apis if a in enabled)
    if missing:
        plan.changes.append(Change(f"enable {', '.join(missing)} in {project}",
                                   ["services", "enable", *missing, f"--project={project}"]))


def _has(policy: Any, role: str, member: str) -> bool:
    """An unconditional binding of *role* that names *member*. A conditional one does not count."""
    for binding in (policy or {}).get("bindings", []) or []:
        if binding.get("role") == role and not binding.get("condition") and member in binding.get("members", []):
            return True
    return False


def _scope_group(kind: str) -> list[str]:
    return {"project": ["projects"], "folder": ["resource-manager", "folders"],
            "organization": ["organizations"]}[kind]


def _ingress_rule(bp: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "kubespace-read",
        "ingressFrom": {"identities": [bp["vpc_sc"]["identity"]], "sources": [{"accessLevel": "*"}]},
        "ingressTo": {"resources": ["*"], "operations": [
            {"serviceName": s, "methodSelectors": [{"method": "*"}]} for s in bp["vpc_sc"]["services"]]},
    }


def _admits(rule: dict[str, Any], identity: str, services: list[str]) -> bool:
    source = rule.get("ingressFrom", {})
    target = rule.get("ingressTo", {})
    named = {op.get("serviceName") for op in target.get("operations", []) or []}
    return identity in (source.get("identities") or []) and set(services) <= named \
        and "*" in (target.get("resources") or [])


def _read_account(cloud: Cloud, reader: str, project: str) -> Any:
    """The reader's description, or None when it does not exist.

    Google answers a describe of a service account that does not exist with PERMISSION_DENIED "(or it may not
    exist)", the same words as a real denial. Listing the project's accounts tells them apart: a list that works
    and omits the reader means it does not exist yet; one that names it, or that is denied too, is a real denial.
    """
    try:
        return cloud.read("iam", "service-accounts", "describe", reader, f"--project={project}")
    except GcloudError as failed:
        if failed.reason != "permission_denied" or "may not exist" not in failed.stderr.lower():
            raise
        listed = cloud.read("iam", "service-accounts", "list", f"--project={project}")  # A denial raises as before.
        if any(isinstance(a, dict) and a.get("email") == reader for a in listed or []):
            raise failed from None
        return None


def plan(bp: dict[str, Any], cloud: Cloud, perimeter: str | None = None) -> Plan:
    """What already matches and what is missing. Reads only."""
    result = Plan()
    pool, reader = bp["pool"], bp["reader"]
    p, pool_id = pool["project_id"], pool["pool_id"]
    seen: set[tuple[str, str]] = set()
    _plan_apis(cloud, result, p, bp["apis"]["pool_project"], seen)
    where = [f"--project={p}", "--location=global"]

    found = cloud.read("iam", "workload-identity-pools", "describe", pool_id, *where)
    if found is None:
        result.changes.append(Change(f"create Workload Identity pool {pool_id} in {p}",
                                     ["iam", "workload-identity-pools", "create", pool_id, *where,
                                      "--display-name=Kubespace"],
                                     undelete=["iam", "workload-identity-pools", "undelete", pool_id, *where],
                                     undeleted=f"Workload Identity pool {pool_id} in {p}"))
    elif found.get("state") == "DELETED":
        result.changes.append(Change(f"restore deleted Workload Identity pool {pool_id} in {p}",
                                     ["iam", "workload-identity-pools", "undelete", pool_id, *where],
                                     undeleted=f"Workload Identity pool {pool_id} in {p}"))
    else:
        result.unchanged.append(f"Workload Identity pool {pool_id} in {p}")

    trust = [f"--issuer-uri={pool['issuer']}", "--attribute-mapping=google.subject=assertion.sub",
             f"--attribute-condition={pool['attribute_condition']}"]
    provider_args = [pool["provider_id"], *where, f"--workload-identity-pool={pool_id}"]
    found_provider = None if found is None else \
        cloud.read("iam", "workload-identity-pools", "providers", "describe", *provider_args)
    update = ["iam", "workload-identity-pools", "providers", "update-oidc", *provider_args, *trust]
    if found_provider is None:
        # Not found may be a provider deleted with its pool: its id is still taken, so the create says it exists.
        # Restore it, then set it to the trust the blueprint names.
        result.changes.append(Change(f"create provider {pool['provider_id']} that accepts only your Kubespace org",
                                     ["iam", "workload-identity-pools", "providers", "create-oidc", *provider_args,
                                      "--display-name=Kubespace org", *trust],
                                     undelete=["iam", "workload-identity-pools", "providers", "undelete",
                                               *provider_args],
                                     then=[update], undeleted=f"provider {pool['provider_id']}"))
    else:
        deleted = found_provider.get("state") == "DELETED"
        if deleted:
            result.changes.append(Change(f"restore deleted provider {pool['provider_id']}",
                                         ["iam", "workload-identity-pools", "providers", "undelete",
                                          *provider_args], undeleted=f"provider {pool['provider_id']}"))
        matches = (found_provider.get("attributeCondition") == pool["attribute_condition"]
                   and found_provider.get("attributeMapping") == pool["attribute_mapping"]
                   and (found_provider.get("oidc") or {}).get("issuerUri") == pool["issuer"])
        if matches and not deleted:
            result.unchanged.append(f"provider {pool['provider_id']} (accepts only your Kubespace org)")
        elif not matches:
            result.changes.append(Change(f"set provider {pool['provider_id']} to accept only your Kubespace org",
                                         update))

    account = _read_account(cloud, reader, p)
    if account is None:
        result.changes.append(Change(f"create service account {reader}",
                                     ["iam", "service-accounts", "create", READER_ACCOUNT_ID, f"--project={p}",
                                      "--display-name=Kubespace reader",
                                      "--description=Read-only access for Kubespace. Only your Kubespace org's "
                                      "subject may impersonate it."]))
    else:
        result.unchanged.append(f"service account {reader}")
        if account.get("disabled"):
            result.changes.append(Change(f"enable service account {reader}",
                                         ["iam", "service-accounts", "enable", reader, f"--project={p}"]))
    sa_policy = None if account is None else cloud.read("iam", "service-accounts", "get-iam-policy", reader,
                                                        f"--project={p}")
    if _has(sa_policy, IMPERSONATION_ROLE, bp["impersonation_member"]):
        result.unchanged.append(f"{IMPERSONATION_ROLE} on {reader} for your Kubespace org only")
    else:
        result.changes.append(Change(f"let only your Kubespace org impersonate {reader}",
                                     ["iam", "service-accounts", "add-iam-policy-binding", reader, f"--project={p}",
                                      f"--role={IMPERSONATION_ROLE}", f"--member={bp['impersonation_member']}"],
                                     after_account_create=account is None))

    kind, scope_id = bp["scope"]["type"], bp["scope"]["id"]
    group = _scope_group(kind)
    member = f"serviceAccount:{reader}"
    policy = cloud.read(*group, "get-iam-policy", scope_id)
    if policy is None:
        raise GcloudError([*group, "get-iam-policy", scope_id], "not_found", f"{kind} {scope_id} was not found")
    for role in bp["roles"]:
        if _has(policy, role, member):
            result.unchanged.append(f"{role} on {kind} {scope_id}")
        else:
            result.changes.append(Change(f"grant {role} on {kind} {scope_id} to {reader}",
                                         [*group, "add-iam-policy-binding", scope_id, f"--member={member}",
                                          f"--role={role}", "--condition=None"],
                                         after_account_create=account is None))

    _plan_apis(cloud, result, bp["quota_project"], bp["apis"]["quota_project"], seen)

    if perimeter:
        _plan_perimeter(bp, cloud, result, perimeter)
    return result


def _plan_perimeter(bp: dict[str, Any], cloud: Cloud, result: Plan, perimeter: str) -> None:
    """Add the ingress rule to the perimeter's enforced config, keeping every rule it already has."""
    match = _PERIMETER.fullmatch(perimeter)
    if match is None:
        raise ValueError("--vpc-sc-perimeter is accessPolicies/<number>/servicePerimeters/<name>")
    policy_id, name = match.groups()
    found = cloud.read("access-context-manager", "perimeters", "describe", name, f"--policy={policy_id}")
    if found is None:
        raise GcloudError(["access-context-manager", "perimeters", "describe", name], "not_found",
                          f"perimeter {perimeter} was not found")
    existing = list((found.get("status") or {}).get("ingressPolicies") or [])
    identity, services = bp["vpc_sc"]["identity"], bp["vpc_sc"]["services"]
    if any(_admits(rule, identity, services) for rule in existing):
        result.unchanged.append(f"VPC Service Controls ingress rule for {bp['reader']} in {name}")
        return
    rules = json.dumps([*existing, _ingress_rule(bp)], indent=2)
    result.changes.append(Change(
        f"add ingress rule kubespace-read for {bp['reader']} to perimeter {name} "
        f"(keeps its {len(existing)} existing rule{'s' if len(existing) != 1 else ''})",
        ["access-context-manager", "perimeters", "update", name, f"--policy={policy_id}",
         "--set-ingress-policies={file}"], file=rules))


# --- Applying -------------------------------------------------------------------------------

def apply(changes: list[Change], run: Runner, out: Any, sleep: Callable[[float], None] = time.sleep) -> int:
    """Make each change in order and print it. Returns how many changed; raises on the first failure."""
    made = 0
    for change in changes:
        path = None
        try:
            if change.file is not None:
                handle, path = tempfile.mkstemp(prefix="kubespace-", suffix=".json")
                with os.fdopen(handle, "w") as written:
                    written.write(change.file)
            args = change.command(path or "")
            delays = iter(CATCH_UP_DELAYS if change.after_account_create else ())
            retried = False
            while True:
                code, _, err = run(args)
                reason = classify(err) if code else None
                delay = next(delays, None) if reason in CATCH_UP_REASONS else None
                if delay is None:
                    break
                retried = True
                sleep(delay)  # IAM takes a while to see a service account created seconds ago.
        finally:
            if path:
                os.unlink(path)
        if reason == "already_exists" and change.undelete and _restore(change, run, out):
            made += 1
            continue
        if reason == "already_exists":
            print(f"  exists     {change.what}", file=out)
            continue
        if code != 0:
            raise GcloudError(args, reason or "error", err, retried)
        made += 1
        if "undelete" in args and change.undeleted:
            print(f"  undeleted  {change.undeleted}", file=out)
        else:
            print(f"  done       {change.what}", file=out)
    return made


def _restore(change: Change, run: Runner, out: Any) -> bool:
    """The create found the id taken: undelete the object, then run ``then``. False when it is not deleted."""
    assert change.undelete is not None
    code, _, err = run(change.undelete)
    if code != 0:
        reason = classify(err)
        if reason in ("permission_denied", "vpc_sc_blocked", "api_not_enabled"):
            raise GcloudError(change.undelete, reason, err)
        return False  # It is not deleted: it exists, as the create said.
    print(f"  undeleted  {change.undeleted}", file=out)
    for args in change.then:
        code, _, err = run(args)
        if code != 0:
            raise GcloudError(args, classify(err), err)
    return True


TTY = "/dev/tty"


def ask(prompt: str) -> str | None:
    """The answer typed at the terminal, or None when there is no terminal to ask."""
    try:
        with open(TTY, "rb+", buffering=0) as tty:
            tty.write(prompt.encode("utf-8"))
            return tty.readline().decode("utf-8", "replace").strip()
    except OSError:
        return None


_HELP = {
    "permission_denied": "Your account may not do this. You need, on the scope and on project P: Service Usage "
                         "Admin, Workload Identity Pool Admin, Service Account Admin and the IAM admin role of "
                         "the scope (Project IAM Admin, Folder IAM Admin or Organization Administrator); for a "
                         "perimeter, Access Context Manager Editor.",
    "vpc_sc_blocked": "A VPC Service Controls perimeter refused Cloud Shell, which is outside every perimeter. "
                      "Run this from a host inside the perimeter's access level, or use the Terraform route.",
    "not_found": "Check the scope and project on the Kubespace setup card.",
}


def _describe(bp: dict[str, Any], perimeter: str | None, out: Any) -> None:
    pool = bp["pool"]
    print("Blueprint from your Kubespace setup code:", file=out)
    print(f"  scope              {bp['scope']['type']} {bp['scope']['id']}", file=out)
    print(f"  pool project (P)   {pool['project_id']} ({pool['project_number']})", file=out)
    print(f"  pool / provider    {pool['pool_id']} / {pool['provider_id']}", file=out)
    print(f"  accepts only       {pool['attribute_condition']}", file=out)
    print(f"  reader             {bp['reader']}", file=out)
    print(f"  read roles         {len(bp['roles'])} on the scope", file=out)
    print(f"  quota project      {bp['quota_project']}", file=out)
    print(f"  VPC-SC perimeter   {perimeter or 'none (add --vpc-sc-perimeter if one surrounds the scope)'}", file=out)
    print("", file=out)


def main(argv: list[str] | None = None, run: Runner = gcloud, answer: Callable[[str], str | None] = ask,
         out: Any = None, sleep: Callable[[float], None] = time.sleep) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Apply Kubespace's keyless read-only access from a blueprint code.")
    parser.add_argument("--code", required=True, help="the blueprint code on your Kubespace setup card (kgb1....)")
    parser.add_argument("--vpc-sc-perimeter", help="accessPolicies/<number>/servicePerimeters/<name> around the scope")
    parser.add_argument("--dry-run", action="store_true", help="print what would change, with the commands; change nothing")
    parser.add_argument("--yes", action="store_true", help="apply without asking")
    args = parser.parse_args(argv)
    try:
        bp = decode(args.code)
        if args.vpc_sc_perimeter and not _PERIMETER.fullmatch(args.vpc_sc_perimeter):
            raise ValueError("--vpc-sc-perimeter is accessPolicies/<number>/servicePerimeters/<name>")
    except ValueError as bad:
        print(f"Refusing: {bad}. Nothing was changed.", file=out)
        return 2
    _describe(bp, args.vpc_sc_perimeter, out)
    cloud = Cloud(run)
    print("Checking what already exists (read-only)...", file=out)
    try:
        result = plan(bp, cloud, args.vpc_sc_perimeter)
    except GcloudError as failed:
        print(f"Could not read: gcloud {shlex.join(failed.args_)} ({failed.reason}).", file=out)
        print(_HELP.get(failed.reason, failed.stderr.strip()[:300]), file=out)
        print("Nothing was changed.", file=out)
        return 4
    for line in result.unchanged:
        print(f"  unchanged  {line}", file=out)
    if not result.changes:
        print(f"\nNothing to change: all {len(result.unchanged)} objects already match the blueprint.", file=out)
        print("Back in Kubespace, verify the connection.", file=out)
        return 0
    for change in result.changes:
        print(f"  to do      {change.what}", file=out)
    if args.dry_run:
        print("\nDry run: nothing was changed. These are the commands it would run:", file=out)
        for change in result.changes:
            print("  gcloud " + shlex.join(change.command()), file=out)
            if change.file is not None:
                print("  # FILE holds the perimeter's ingress rules with kubespace-read added:", file=out)
                print("\n".join("  # " + line for line in change.file.splitlines()), file=out)
        return 0
    if not args.yes:
        reply = answer(f"\nMake these {len(result.changes)} changes? Type yes to apply: ")
        if reply is None:
            print("\nNo terminal to ask on, so nothing was changed. Run it again in an interactive shell, "
                  "or add --yes.", file=out)
            return 3
        if reply.lower() != "yes":
            print("Nothing was changed.", file=out)
            return 1
    print("Applying...", file=out)
    try:
        made = apply(result.changes, run, out, sleep)
    except GcloudError as failed:
        print(f"Stopped: gcloud {shlex.join(failed.args_)} failed ({failed.reason}).", file=out)
        if failed.retried and failed.reason == "permission_denied":
            print("It was retried for a minute: IAM may not have caught up yet with the service account created "
                  "just before. Wait a few minutes and run it again. If it still stops, the cause is a missing role:",
                  file=out)
        print(_HELP.get(failed.reason, failed.stderr.strip()[:300]), file=out)
        print("The changes printed above as done stay. Fix the cause and run it again: it picks up from here.",
              file=out)
        return 4
    print(f"\nMade {made} change{'s' if made != 1 else ''}; {len(result.unchanged)} already matched. "
          "Running it again changes nothing.", file=out)
    print("Back in Kubespace, verify the connection.", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
