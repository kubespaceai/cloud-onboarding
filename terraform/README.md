# Kubespace org-wide onboarding

This Terraform module connects many cloud accounts to Kubespace at once, through
your own change process. Each submodule grants read access once, at the top of a
tree, to your organization's own Kubespace identity. No access key, client secret
or service-account key is created, and nothing is stored at Kubespace.

| Submodule | Grants | Covers |
| --- | --- | --- |
| `aws` | Role `KubespaceReadOnly` (SecurityAudit, ViewOnlyAccess) in this account, and a service-managed StackSet that deploys the same role (`role.yaml`) to every account of the units you choose | Every account in those units, including accounts added later. Accounts that leave lose the role. |
| `azure` | A user-assigned managed identity with a federated credential for your Kubespace subject, and **Reader** on a management group | Every subscription under the group, including subscriptions added later |
| `gcp` | A Workload Identity pool, provider and `kubespace-reader` service account in a project of your organization, that only your Kubespace org's subject may impersonate; the read roles of a single-project connection (including `roles/orgpolicy.policyViewer`) to that reader on a project, or plus `roles/browser` on a folder or organization; the read APIs; optionally a VPC Service Controls ingress rule | The project, or every project under the folder or organization, including projects added later |

Use only the submodules you need; each one needs only its own provider.

## Use it

Reference each submodule from this repository with a release tag, never `main`:

```hcl
module "kubespace_gcp" {
  source = "git::https://github.com/kubespaceai/cloud-onboarding.git//terraform/gcp?ref=v1.2.0"

  org_subject         = "<subject Kubespace gives you>"
  pool_project_id     = "<project P that holds the pool>"
  pool_project_number = "<project number of P>"
  pool_id             = "<pool ID Kubespace gives you>"

  scope_type       = "folder" # or "organization", or "project" with a project ID
  scope_id         = "<numeric folder ID>"
  quota_project_id = "<project ID>"

  vpc_sc_perimeter = null # or "accessPolicies/<n>/servicePerimeters/<name>"
}
```

### Google Cloud identity (module 1.2)

Since module 1.2 the `gcp` submodule grants the read roles to `kubespace-reader`,
a service account in a project P of your own organization, so Domain Restricted
Sharing needs no exception for Kubespace. It creates, in P:

- the APIs the exchange needs (IAM, IAM Credentials, STS, Cloud Resource Manager);
- Workload Identity pool `pool_id` and OIDC provider `kubespace-org` for
  `https://accounts.google.com`, mapping `google.subject` to `assertion.sub` with
  the condition `assertion.sub == '<org_subject>'`;
- `kubespace-reader`, which only that one subject may impersonate
  (`roles/iam.workloadIdentityUser` for
  `principal://iam.googleapis.com/projects/<P number>/locations/global/workloadIdentityPools/<pool_id>/subject/<org_subject>`).

One pool serves every scope of your Kubespace org. When you connect several
scopes, set `create_pool = false` in every module call but one.

`vpc_sc_perimeter` adds an ingress rule to that perimeter that admits
`kubespace-reader` only, to the read services only. If your perimeter's ingress
policies are managed inline in another resource, add the same rule there
instead (output `reader_service_account` names the identity).

Module 1.1 and earlier granted your Kubespace org's own service account
(`org_service_account`). That input still works for folder and organization
scopes, so existing calls apply unchanged; module 1.2 also enables the remaining
read APIs on the quota project.

The paths are `terraform/aws`, `terraform/azure` and `terraform/gcp`. Their
inputs and outputs are stable within a major version. `examples/basic/main.tf`
applies all three; replace every value and delete the submodules you don't use.

Release tags (`vX.Y.Z`) and release branches (`release-x.y`) can't be changed or
deleted once created. Version 1.0.0 is the same module that the Kubespace
documentation published as files to copy; a copy you made of it keeps working.

## Inputs from Kubespace

`GET /api/v1/cloud-org-scopes/{aws|azure|gcp}/identity` returns what each grant
names: `subject` and `audience` for AWS, `subject` for Azure, and `subject` (or
`email` for module 1.1 calls) for Google Cloud. The Google Cloud setup gives the
pool ID. None of them is a secret.

## Connect to Kubespace

Each submodule's `kubespace_setup` output is the body of
`POST /api/v1/cloud-org-scopes/{provider}/setup` (one body per unit for AWS).
Send it, then call `POST /api/v1/cloud-org-scopes/{id}/verify`. Kubespace lists
the members as your organization's identity and connects each one. Removed
accounts show as no longer connected.

## Requirements

- AWS: apply in the management account, or in a delegated StackSets
  administrator with `call_as = "DELEGATED_ADMIN"`. Trusted access for StackSets
  with AWS Organizations must be on. The management account itself is not a
  StackSet target; this module creates its role directly.
- Azure: configure the `azurerm` provider for the subscription that holds the
  managed identity. You need permission to assign roles on the management group.
- Google Cloud: `quota_project_id` is a project under the scope that Kubespace's
  read calls bill (for a project scope, the project itself). You need permission
  to set IAM policy on the scope, and to create the pool and service account in
  P. The ingress rule needs Access Context Manager editor on the policy.

## Tests

Each submodule has offline tests with mocked providers, no cloud credentials:

```bash
cd terraform/gcp && terraform init -backend=false && terraform test
```

CI runs `terraform fmt`, `validate` and `test` for every submodule on each pull
request.

## Revoke

`terraform destroy`, or remove the grant in your cloud. Kubespace detects the
revocation on its next check.
