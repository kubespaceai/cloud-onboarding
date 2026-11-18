# AWS

| File | What it is |
| --- | --- |
| `preflight.sh`, `preflight.py` | Read-only pre-flight: shows Kubespace your AWS organization before you grant anything |
| `tests/` | Tests of the script against recorded AWS CLI output |

The read-only role itself is created by the CloudFormation template Kubespace
links to, or by the Terraform module in [`terraform/aws`](../terraform/aws).

## Read-only pre-flight

The pre-flight runs under **your** AWS CLI session and only reads. Every call
is an `aws sts get-caller-identity`, an `aws account list-regions` or an
`aws organizations describe-…` or `list-…`. It creates no role, attaches no
policy, enables nothing and deploys nothing.

For the scope you name (`organization`, `organizationalUnits/<r-… or ou-…>`
or `accounts/<account-id>`) it collects:

- the account you are signed in to (its ID only, never your user or role
  name) and its AWS Organizations organization: ID, management account,
  feature set, root and which of SCPs and RCPs are enabled;
- the organizational units and the member accounts in scope, with their
  status. No account email is read into the summary;
- the Regions enabled in your account;
- the Deny statements of service control policies (SCPs) and resource control
  policies (RCPs) attached to the scope, to the units above it or to anything
  in it, that would deny Kubespace's role being assumed
  (`sts:AssumeRoleWithWebIdentity`) or its reads (a fixed list of read
  actions such as `ec2:DescribeInstances` and `eks:DescribeCluster`). A
  statement with a condition, or one narrowed to some resources or
  principals, is marked as conditional: it may not apply to Kubespace's role.
  The summary names the policy, the statement and the matched actions, never
  the policy text.

It prints the exact JSON it would send, then asks. Only `yes` sends it, with
your single-use pairing code, to the Kubespace address on your setup card. It
refuses any address outside `kubespace.ai`. It never sends access keys,
session tokens, your user name or anything outside the list above. Whatever
you can't read is reported as unknown with the reason (`permission_denied`,
`not_found`, `not_signed_in`, `not_in_organization`), never as absent.

### Run it

Open [AWS CloudShell](https://console.aws.amazon.com/cloudshell/home). It is
already signed in as you. Paste the command from the Kubespace setup card: it
clones this repository at the release Kubespace names, prints that release,
and runs the pre-flight with your pairing code. The code works once and for 15
minutes.

Any other shell works too, signed in with the AWS CLI v2. It needs `aws`,
`python3` (3.9 or later) and `git`:

```bash
git clone --depth 1 --branch <release> https://github.com/kubespaceai/cloud-onboarding.git kubespace-preflight
bash kubespace-preflight/aws/preflight.sh --scope organization \
  --endpoint https://app.kubespace.ai/api/v1/cloud-org-scopes/gcp/preflight \
  --code ksp_XXXXXXXXXXXXXXXXXXXXXXXXXX
```

Kubespace takes every provider's pre-flight at the same address. Add
`--dry-run` to print the summary and send nothing.

### What to read

Run it in the management account, or in a delegated administrator account for
AWS Organizations, to see the whole organization and its policies. In a member
account it sees the organization's ID and its own account and Regions; the
units, the other accounts and the policies are reported as unknown. Outside an
organization it reads your account only.
