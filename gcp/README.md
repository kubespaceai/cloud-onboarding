# Google Cloud

| File | What it is |
| --- | --- |
| `preflight.sh`, `preflight.py` | Read-only pre-flight: shows Kubespace your estate before you grant anything |
| `preflight.md` | Cloud Shell tutorial for the pre-flight |
| `tests/` | Tests of the pre-flight against recorded `gcloud` output |

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

### Tests

```bash
python3 -m unittest discover -s gcp/tests
```
