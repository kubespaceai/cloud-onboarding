# Google Cloud

| File | What it is |
| --- | --- |
| `preflight.sh`, `preflight.py` | Read-only pre-flight: shows Kubespace your estate before you grant anything |
| `preflight.md` | Cloud Shell tutorial for the pre-flight |
| `apply.sh`, `apply.py` | Applies the keyless read-only access from the blueprint code on the setup card |
| `apply.md` | Cloud Shell tutorial for the apply script |
| `tests/` | Tests of both scripts against recorded `gcloud` output |

## Read-only pre-flight

The pre-flight runs under **your** `gcloud` identity and only reads. For the
scope you name (`organizations/<id>`, `folders/<id>` or `projects/<id>`) it
collects:

- the organization, folders and projects;
- GKE clusters: private or public, Autopilot or Standard, DNS endpoint and
  release channel;
- organization policies, including `iam.allowedPolicyMemberDomains` and
  `iam.managed.allowedPolicyMembers`;
- VPC Service Controls perimeters that hold a project in scope, naming only
  those projects;
- which of the APIs Kubespace reads are enabled in each project.

It prints the exact JSON it would send, then asks. Only `yes` sends it, with
your single-use pairing code, to the Kubespace address on your setup card. It
refuses any address outside `kubespace.ai`. It never sends credentials,
tokens, labels or anything outside the list above. Whatever you can't read is
reported as unknown with the reason (`permission_denied`, `vpc_sc_blocked`,
`api_not_enabled`, `not_found`), never as absent.

### Two ways to run it

**Cloud Shell.** Choose **Open the pre-flight in Cloud Shell** on the
Kubespace setup card, then follow the tutorial.

**A workstation or VM inside your VPC Service Controls perimeter.** VPC
Service Controls treats Cloud Shell as outside every perimeter. If your
projects sit in a perimeter, run the pre-flight from a host that meets the
perimeter's access level (for example a corporate workstation or a VM in the
perimeter). The host needs `gcloud`, `python3` (3.9 or later) and `git`:

```bash
gcloud auth login
git clone --depth 1 https://github.com/kubespaceai/cloud-onboarding.git kubespace-preflight
bash kubespace-preflight/gcp/preflight.sh --scope folders/123456789012 \
  --endpoint https://app.kubespace.ai/api/v1/cloud-org-scopes/gcp/preflight \
  --code ksp_XXXXXXXXXXXXXXXXXXXXXXXXXX
```

The command on the setup card does the same in one line. Add `--dry-run` to
print the summary and send nothing.

## Apply the read-only access

`apply.sh` creates the same objects as the Terraform module in
`terraform/gcp`, under **your** `gcloud` identity. Kubespace receives nothing
from it. It takes one argument, the blueprint code on the Kubespace setup card:

```text
kgb1.<p|f|o>.<scope id>.<quota project|->.<pool project|->.<pool project number>.<pool id>.<subject>.<checksum>
```

The code names the scope (project, folder or organization), the quota
project, project P that holds the pool, the pool and your Kubespace
organization's Google subject. `-` means "the same project as before". The
roles, the APIs and the VPC Service Controls rule are fixed by the code's
version (`kgb1`) and listed at the top of `apply.py`; a test pins them to the
Terraform module. A mistyped code fails its checksum and nothing runs.

The script reads first, prints what is missing and asks. Only `yes` applies.
It only adds: it never removes a binding, a rule or an object. It prints each
change, and running it again prints `Nothing to change`.

```bash
gcloud auth login
git clone --depth 1 --branch v1.3.0 https://github.com/kubespaceai/cloud-onboarding.git kubespace-onboarding
bash kubespace-onboarding/gcp/apply.sh --code kgb1.f.123456789012.my-quota-project.-.222222222222.kubespace-0123456789.104712345678901234567.abc123
```

Options: `--vpc-sc-perimeter accessPolicies/<number>/servicePerimeters/<name>`
adds the ingress rule for `kubespace-reader` to that perimeter, keeping its
other rules. `--dry-run` prints the changes and the exact `gcloud` commands
and makes none. `--yes` applies without asking.

The roles you need: Service Usage Admin and Workload Identity Pool Admin and
Service Account Admin on P, the IAM admin role of the scope, and Access
Context Manager Editor for a perimeter.

### Tests

```bash
python3 -m unittest discover -s gcp/tests
```
