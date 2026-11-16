# Azure

| File | What it is |
| --- | --- |
| `preflight.sh`, `preflight.py` | Read-only pre-flight: shows Kubespace your Azure directory before you grant anything |
| `tests/` | Tests of the script against recorded `az` output |

## Read-only pre-flight

The pre-flight runs under **your** Azure CLI session and only reads. Every
call is an `az ... list`, `az ... show` or an `az rest --method get`. It
creates no identity, assigns no role, registers no resource provider and
deploys nothing.

For the scope you name (`directory`, `managementGroups/<id>` or
`subscriptions/<subscription-id>`) it collects:

- the directory (Microsoft Entra tenant) you are signed in to;
- the management groups and the subscriptions under them;
- Azure Policy assignments, inherited ones included, whose `deny` effect
  would stop the setup: creating the user-assigned managed identity, its
  federated credential, the Reader role assignment or the deployment. An
  assignment in `DoNotEnforce` mode is listed as not enforced;
- which of the resource providers Kubespace reads through
  (`Microsoft.ContainerService`, `Microsoft.KubernetesConfiguration`,
  `Microsoft.ManagedIdentity`) are not registered in each subscription.

It prints the exact JSON it would send, then asks. Only `yes` sends it, with
your single-use pairing code, to the Kubespace address on your setup card. It
refuses any address outside `kubespace.ai`. It never sends credentials,
tokens, your user name or anything outside the list above. Whatever you can't
read is reported as unknown with the reason (`permission_denied`,
`not_found`, `not_signed_in`), never as absent.

### Run it

Open [Azure Cloud Shell](https://shell.azure.com/bash) (Bash). It is already
signed in as you. Paste the command from the Kubespace setup card: it clones
this repository at the release Kubespace names, prints that release, and runs
the pre-flight with your pairing code. The code works once and for 15 minutes.

Any other shell works too, after `az login`. It needs `az`, `python3` (3.9 or
later) and `git`:

```bash
az login
git clone --depth 1 --branch <release> https://github.com/kubespaceai/cloud-onboarding.git kubespace-preflight
bash kubespace-preflight/azure/preflight.sh --scope directory \
  --endpoint https://app.kubespace.ai/api/v1/cloud-org-scopes/gcp/preflight \
  --code ksp_XXXXXXXXXXXXXXXXXXXXXXXXXX
```

Kubespace takes every provider's pre-flight at the same address. Add
`--dry-run` to print the summary and send nothing.

### What to read

Reader on the scope is enough for everything except the management-group
tree, which needs Management Group Reader (or Reader at the root group).
Without it the subscriptions you can see in the directory are listed without
their group.
