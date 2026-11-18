#!/usr/bin/env python3
"""Kubespace read-only pre-flight for AWS.

Runs under your own AWS CLI session (AWS CloudShell, or any shell signed in with the AWS CLI v2) and
only reads. For the scope you name it collects:

- the account you are signed in to, and its AWS Organizations organization;
- the organizational units and the member accounts in scope;
- the Regions enabled in your account;
- service control policies (SCPs) and resource control policies (RCPs) attached in scope whose Deny
  statements would stop Kubespace's role being assumed (``sts:AssumeRoleWithWebIdentity``) or its reads.

It prints the exact JSON it would send, then asks before sending it to Kubespace with your
single-use pairing code. It never sends credentials, keys or tokens, and it never creates, attaches
or changes anything. Anything you can't read is reported as unknown, with the reason, never as absent.

Standard library only. Needs python3 and aws (CLI v2) on PATH.
"""

from __future__ import annotations

import argparse
import fnmatch
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

SCHEMA = "kubespace.aws-preflight/v1"
# What a deny can stop: the role being assumed through the org's Google identity, and what it reads.
TRUST_ACTIONS = ("sts:AssumeRoleWithWebIdentity",)
# Representative reads of Kubespace's read-only role (SecurityAudit and ViewOnlyAccess). Only these are matched.
READ_ACTIONS = ("sts:GetCallerIdentity", "iam:GetRole", "iam:ListRoles", "iam:ListAttachedRolePolicies",
                "organizations:ListAccounts", "organizations:ListPolicies", "ec2:DescribeRegions",
                "ec2:DescribeInstances", "ec2:DescribeVpcs", "eks:ListClusters", "eks:DescribeCluster",
                "s3:ListAllMyBuckets")
POLICY_TYPES = {"SERVICE_CONTROL_POLICY": "scp", "RESOURCE_CONTROL_POLICY": "rcp"}
MAX_UNITS = 500
MAX_ACCOUNTS = 1000
MAX_POLICIES = 200
CODE = re.compile(r"ksp_[A-Z2-7]{26}\Z")
ACCOUNT = r"[0-9]{12}"
ROOT = r"r-[0-9a-z]{4,32}"
UNIT = r"ou-[0-9a-z]{4,32}-[0-9a-z]{8,32}"
ORG = re.compile(r"o-[a-z0-9]{10,32}\Z")
POLICY = re.compile(r"p-[0-9A-Za-z_]{8,128}\Z")
SID = re.compile(r"[A-Za-z0-9]{1,128}\Z")
REGION = re.compile(r"[a-z]{2}(-[a-z]+)+-[0-9]{1,2}\Z")
REGION_STATES = ("ENABLED", "ENABLING", "DISABLING", "DISABLED", "ENABLED_BY_DEFAULT")
ACCOUNT_STATES = ("ACTIVE", "SUSPENDED", "PENDING_CLOSURE")
SCOPE = re.compile(rf"organization\Z|organizationalUnits/({ROOT}|{UNIT})\Z|accounts/({ACCOUNT})\Z")
ENDPOINT_HOST = re.compile(r"([a-z0-9-]+\.)*kubespace\.ai\Z")
# Never sent, whatever a field holds: AWS access key ids, secret keys and session tokens, OAuth and
# web identity tokens, JWTs and private keys.
SECRET = re.compile(r"(AKIA|ASIA)[0-9A-Z]{16}|aws_secret_access_key|secretaccesskey|session_?token|ya29\.|"
                    r"eyJ[A-Za-z0-9_-]{10,}\.|-----BEGIN|private_key|client_secret|refresh_token|access_token",
                    re.IGNORECASE)
_DISPLAY = re.compile(r"[^\x00-\x1f\x7f]{1,120}\Z")

Runner = Callable[[list[str]], tuple[int, str, str]]


class Unreadable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def classify(stderr: str) -> str:
    """Why the AWS CLI could not read something, as one of the reasons Kubespace accepts."""
    text = stderr.lower()
    if "awsorganizationsnotinuse" in text or "not a member of an organization" in text:
        return "not_in_organization"
    if ("unable to locate credentials" in text or "expiredtoken" in text or "invalidclienttokenid" in text
            or "aws sso login" in text):
        return "not_signed_in"
    if "accessdenied" in text or "unauthorizedoperation" in text or "not authorized" in text or "403" in text:
        return "permission_denied"
    if "notfound" in text or "not found" in text or "does not exist" in text:
        return "not_found"
    return "error"


def aws(args: list[str]) -> tuple[int, str, str]:
    done = subprocess.run(["aws", *args, "--output", "json", "--no-cli-pager"], capture_output=True, text=True,
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
            return json.loads(out or "null") or {}
        except ValueError:
            raise Unreadable("error") from None


def _display(value: Any) -> str | None:
    return value if isinstance(value, str) and _DISPLAY.fullmatch(value) and not SECRET.search(value) else None


def _match(pattern: str, value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def _section(read: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"status": "read", **read()}  # read() may set status "partial" with its reason
    except Unreadable as refused:
        return {"status": "unknown", "reason": refused.reason}


def _parallel(fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(fn, items))


# --- The organization, its units and accounts ----------------------------------------------------


def organization_of(reader: Reader) -> dict[str, Any]:
    org = (reader.json("organizations", "describe-organization") or {}).get("Organization") or {}
    if not (org_id := org.get("Id")) or not ORG.fullmatch(str(org_id)):
        raise Unreadable("error")
    found: dict[str, Any] = {"id": org_id}
    if management := _match(ACCOUNT, org.get("MasterAccountId")):
        found["management_account_id"] = management
    if org.get("FeatureSet") in ("ALL", "CONSOLIDATED_BILLING"):
        found["feature_set"] = org["FeatureSet"]
    try:
        roots = reader.json("organizations", "list-roots").get("Roots") or []
        root = roots[0] if roots else {}
        if root_id := _match(ROOT, root.get("Id")):
            found["root_id"] = root_id
            found["policy_types"] = sorted(p["Type"] for p in root.get("PolicyTypes") or []
                                           if p.get("Status") == "ENABLED" and p.get("Type") in POLICY_TYPES)
    except Unreadable as refused:
        found.update({"status": "partial", "reason": refused.reason})
    return found


def _account(listed: dict[str, Any], parent: str | None) -> dict[str, Any] | None:
    account_id = _match(ACCOUNT, listed.get("Id"))
    if not account_id:
        return None
    item: dict[str, Any] = {"account_id": account_id}
    if listed.get("Status") in ACCOUNT_STATES:
        item["status"] = listed["Status"]
    if parent:
        item["parent"] = parent
    if name := _display(listed.get("Name")):
        item["display_name"] = name
    return item


def tree(reader: Reader, start: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The units under ``start`` (a root or unit) and the accounts in them, as two sections. Breadth first."""
    units: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []
    reasons: list[str] = []
    truncated = False
    level = [start]

    def children(parent: str) -> tuple[str, list[Any] | None, list[Any] | None, str | None]:
        try:
            found_units = reader.json("organizations", "list-organizational-units-for-parent", "--parent-id",
                                      parent).get("OrganizationalUnits") or []
            found_accounts = reader.json("organizations", "list-accounts-for-parent", "--parent-id",
                                         parent).get("Accounts") or []
            return parent, found_units, found_accounts, None
        except Unreadable as refused:
            return parent, None, None, refused.reason

    while level:
        next_level: list[str] = []
        for parent, found_units, found_accounts, reason in _parallel(children, level):
            if reason:
                if parent == start:
                    raise Unreadable(reason)
                reasons.append(reason)
                continue
            for unit in found_units or []:
                if not (unit_id := _match(UNIT, unit.get("Id"))):
                    continue
                if len(units) >= MAX_UNITS:
                    truncated = True
                    break
                item: dict[str, Any] = {"id": unit_id, "parent": parent}
                if name := _display(unit.get("Name")):
                    item["display_name"] = name
                units.append(item)
                next_level.append(unit_id)
            for listed in found_accounts or []:
                if len(accounts) >= MAX_ACCOUNTS:
                    truncated = True
                    break
                if account := _account(listed, parent):
                    accounts.append(account)
        level = next_level
    unit_section: dict[str, Any] = {"items": units}
    account_section: dict[str, Any] = {"items": accounts}
    for section in (unit_section, account_section):
        if truncated:
            section["truncated"] = True
        if reasons:
            section.update({"status": "partial", "reason": reasons[0]})
    return unit_section, account_section


def ancestors(reader: Reader, child: str) -> list[str]:
    """The units and root above a unit or account, nearest first. Empty when they can't be read."""
    found: list[str] = []
    current = child
    for _ in range(8):  # AWS Organizations nests at most five units under the root.
        try:
            parents = reader.json("organizations", "list-parents", "--child-id", current).get("Parents") or []
        except Unreadable:
            return found
        parent = parents[0].get("Id") if parents else None
        if not (isinstance(parent, str) and re.fullmatch(f"{ROOT}|{UNIT}", parent)):
            return found
        found.append(parent)
        if parent.startswith("r-"):
            return found
        current = parent
    return found


# --- Regions -------------------------------------------------------------------------------------


def regions(reader: Reader, account_id: str) -> dict[str, Any]:
    listed = reader.json("account", "list-regions").get("Regions") or []
    items = [{"region": r["RegionName"], "status": r["RegionOptStatus"]} for r in listed
             if isinstance(r, dict) and REGION.fullmatch(str(r.get("RegionName", "")))
             and r.get("RegionOptStatus") in REGION_STATES]
    return {"account_id": account_id, "items": sorted(items, key=lambda r: r["region"])[:60]}


# --- SCP and RCP denies --------------------------------------------------------------------------


def _list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [v for v in values if isinstance(v, str)]


def _matches(patterns: list[str], action: str) -> bool:
    return any(fnmatch.fnmatchcase(action.lower(), p.lower()) for p in patterns)


def statement_denies(statement: dict[str, Any]) -> tuple[list[str], list[str]]:
    """For one Deny statement: the trust actions and read actions it denies (before any condition)."""
    if str(statement.get("Effect", "")) != "Deny":
        return [], []
    if "NotAction" in statement:
        excluded = _list(statement["NotAction"])
        hit = lambda action: not _matches(excluded, action)  # noqa: E731
    else:
        listed = _list(statement.get("Action"))
        hit = lambda action: _matches(listed, action)  # noqa: E731
    return [a for a in TRUST_ACTIONS if hit(a)], [a for a in READ_ACTIONS if hit(a)]


def _conditional(statement: dict[str, Any]) -> bool:
    """A condition, a narrowed resource or principal: the deny may not apply to Kubespace's role."""
    if statement.get("Condition") or "NotResource" in statement or "NotPrincipal" in statement:
        return True
    resources = _list(statement.get("Resource", "*"))
    principal = statement.get("Principal", "*")
    return resources != ["*"] or principal not in ("*", {"AWS": "*"})


def policy_denies(reader: Reader, types: list[str], in_scope: set[str]) -> dict[str, Any]:
    """Deny statements of SCPs and RCPs attached to the scope, the units above it or anything in it."""
    listings: list[tuple[str, dict[str, Any]]] = []
    for policy_type in types:
        for listed in reader.json("organizations", "list-policies", "--filter", policy_type).get("Policies") or []:
            if isinstance(listed, dict) and POLICY.fullmatch(str(listed.get("Id", ""))):
                listings.append((policy_type, listed))
    unreadable: list[dict[str, str]] = []

    def one(listing: tuple[str, dict[str, Any]]) -> list[dict[str, Any]] | dict[str, str]:
        policy_type, listed = listing
        policy_id = listed["Id"]
        try:
            attached = reader.json("organizations", "list-targets-for-policy", "--policy-id", policy_id
                                   ).get("Targets") or []
            targets = sorted({t.get("TargetId") for t in attached if t.get("TargetId") in in_scope})
            if not targets:
                return []
            content = reader.json("organizations", "describe-policy", "--policy-id", policy_id
                                  ).get("Policy", {}).get("Content")
            document = json.loads(content) if isinstance(content, str) else {}
        except Unreadable as refused:
            return {"policy_id": policy_id, "reason": refused.reason}
        except ValueError:
            return {"policy_id": policy_id, "reason": "error"}
        statements = document.get("Statement") if isinstance(document, dict) else None
        found = []
        for index, statement in enumerate(statements if isinstance(statements, list) else [statements]):
            if not isinstance(statement, dict):
                continue
            trust, reads = statement_denies(statement)
            if not trust and not reads:
                continue
            item: dict[str, Any] = {
                "policy_id": policy_id, "type": POLICY_TYPES[policy_type], "aws_managed": bool(listed.get("AwsManaged")),
                "targets": targets[:50], "statement": index,
                "denies": [d for d, hit in (("trust", trust), ("read", reads)) if hit],
                "actions": (trust + reads)[:20], "conditional": _conditional(statement)}
            if isinstance(statement.get("Sid"), str) and SID.fullmatch(statement["Sid"]):
                item["sid"] = statement["Sid"]
            if name := _display(listed.get("Name")):
                item["name"] = name
            found.append(item)
        return found

    items: list[dict[str, Any]] = []
    for result in _parallel(one, listings[:MAX_POLICIES]):
        if isinstance(result, dict):
            unreadable.append(result)
        else:
            items.extend(result)
    out: dict[str, Any] = {"items": items[:500], "unreadable": unreadable}
    if unreadable:
        out.update({"status": "partial", "reason": unreadable[0]["reason"]})
    if len(listings) > MAX_POLICIES:
        out["truncated"] = True
    return out


# --- The summary ---------------------------------------------------------------------------------


def collect(scope: str, run: Runner = aws, now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    match = SCOPE.fullmatch(scope)
    if not match:
        raise ValueError("scope must be organization, organizationalUnits/<r-… or ou-…> or accounts/<account-id>")
    reader = Reader(run)
    try:
        caller = _match(ACCOUNT, reader.json("sts", "get-caller-identity").get("Account"))
    except Unreadable:
        caller = None
    if not caller:
        raise SystemExit("Sign in first: AWS CloudShell signs you in already; elsewhere run aws configure sso or aws login.")
    organization = _section(lambda: organization_of(reader))
    in_org = organization["status"] != "unknown"
    stamp = (now or (lambda: datetime.now(timezone.utc)))().strftime("%Y-%m-%dT%H:%M:%SZ")

    unit_id, account_id = match.group(1), match.group(2)
    lone = {"status": "read", "items": [{"account_id": account_id or caller}]}
    no_units: dict[str, Any] = {"status": "read", "items": []}
    above: list[str] = []
    if unit_id:
        scope_out = {"type": "organizational_unit", "id": unit_id}
        units, accounts = _tree(reader, unit_id, organization)
        if unit_id.startswith("ou-"):
            above = ancestors(reader, unit_id)
    elif account_id or not in_org or not organization.get("root_id"):
        # One account, or no organization to read: the account named, else your own.
        target = account_id or caller
        scope_out = {"type": "account", "id": target}
        units, accounts = no_units, lone
        if in_org:
            accounts = {"status": "read", "items": [_described(reader, target)]}
            above = ancestors(reader, target)
    else:
        scope_out = {"type": "organization", "id": organization["id"]}
        units, accounts = _tree(reader, organization["root_id"], organization)

    if not in_org:
        denies: dict[str, Any] = {"status": "unknown", "reason": organization["reason"]}
    else:
        in_scope = {scope_out["id"], *above, *(u["id"] for u in units.get("items", [])),
                    *(a["account_id"] for a in accounts.get("items", []))}
        if scope_out["type"] == "organization":
            in_scope.add(organization["root_id"])
        types = organization.get("policy_types")
        denies = _section(lambda: policy_denies(reader, types, in_scope)) if types is not None else {
            "status": "unknown", "reason": organization.get("reason", "error")}
    summary = {
        "schema": SCHEMA,
        "generated_at": stamp,
        "scope": scope_out,
        "caller": {"account_id": caller},
        "organization": organization,
        "organizational_units": units,
        "accounts": accounts,
        "regions": _section(lambda: regions(reader, caller)),
        "policy_denies": denies,
    }
    return _clean(summary)


def _tree(reader: Reader, start: str, organization: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if organization["status"] == "unknown":
        reason = organization["reason"]
        return {"status": "unknown", "reason": reason}, {"status": "unknown", "reason": reason}
    try:
        units, accounts = tree(reader, start)
    except Unreadable as refused:
        return {"status": "unknown", "reason": refused.reason}, {"status": "unknown", "reason": refused.reason}
    return {"status": "read", **units}, {"status": "read", **accounts}


def _described(reader: Reader, account_id: str) -> dict[str, Any]:
    try:
        listed = reader.json("organizations", "describe-account", "--account-id", account_id).get("Account") or {}
    except Unreadable:
        listed = {}
    return _account({**listed, "Id": account_id}, None) or {"account_id": account_id}


def _clean(summary: dict[str, Any]) -> dict[str, Any]:
    for section in summary.values():
        if isinstance(section, dict) and section.get("status") == "unknown":
            for key in [k for k in section if k not in ("status", "reason")]:
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


def main(argv: list[str] | None = None, run: Runner = aws,
         send: Callable[[str, str, dict[str, Any]], tuple[int, str]] = post,
         answer: Callable[[str], str | None] = ask, out: Any = None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Kubespace read-only AWS pre-flight.")
    parser.add_argument("--scope", default="organization",
                        help="organization, organizationalUnits/<r-… or ou-…> or accounts/<account-id>")
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
