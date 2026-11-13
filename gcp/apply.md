# Connect Google Cloud to Kubespace (read-only, keyless)

## Before you start

<walkthrough-tutorial-duration duration="10"></walkthrough-tutorial-duration>

This walkthrough gives Kubespace read-only access to the project, folder or
organization on your Kubespace setup card. It runs one script under **your**
identity. Kubespace receives nothing from it: no key, no password, no token.

The script creates, in a project of your own organization (P):

- a Workload Identity pool and a provider that accepts only your Kubespace
  organization's Google identity;
- a `kubespace-reader` service account that only that identity may
  impersonate;

then grants `kubespace-reader` Kubespace's read roles on the scope and enables
the read APIs. These are the same objects as the Kubespace Terraform module in
`terraform/gcp`.

You need:

- the **blueprint code** from the Kubespace setup card. It starts with `kgb1.`
  and names your scope and pool. It is not a secret;
- the right to change IAM on the scope and on project P. The script names the
  missing role if a step is refused.

**Behind VPC Service Controls?** Cloud Shell is outside every perimeter. If a
perimeter surrounds your projects, use the Terraform route on the setup card,
or run the script from a host inside the perimeter's access level (see the
README in this folder).

## Open the terminal

If the terminal is not open at the bottom of the console, click
<walkthrough-spotlight-pointer spotlightId="devshell-activate-button">Activate Cloud Shell</walkthrough-spotlight-pointer>.

Cloud Shell opened this repository in a temporary session with no access to
your credentials. Sign in with your own account and follow the link it prints:

```bash
gcloud auth login
```

Check the commit you are running. It should match the commit the Kubespace
setup card names:

```bash
git rev-parse HEAD
```

## Apply the blueprint

Paste the command from the Kubespace setup card. It comes down to:

```bash
bash gcp/apply.sh --code kgb1.f.123456789012.my-quota-project.-.222222222222.kubespace-0123456789.104712345678901234567.abc123
```

The script prints the blueprint the code names, reads what already exists and
lists what is missing. Nothing changes until you type `yes`.

If a VPC Service Controls perimeter surrounds the scope, add
`--vpc-sc-perimeter accessPolicies/<number>/servicePerimeters/<name>`. The
script adds one ingress rule that admits `kubespace-reader` only, to the read
services only, and keeps every rule the perimeter already has.

To see the changes and the exact `gcloud` commands without making them, add
`--dry-run`.

## Run it again

Run the same command again. Every object now matches, so it prints
`Nothing to change` and changes nothing. If a step was refused the first time,
this run picks up from there.

## Reconnecting a project you revoked

When you revoke a connection, Google keeps the pool and provider for 30 days
as deleted objects, and a reconnect uses the same names. The script restores
them (it prints `undeleted` and names each one) and sets the provider back to
accept only your Kubespace org. If `kubespace-reader` does not exist yet, the
script creates it; it does not stop for a permission error. One run is enough:
there is nothing to undelete by hand.

## Check it in the console

Open the
<walkthrough-spotlight-pointer spotlightId="console-nav-menu">Navigation menu</walkthrough-spotlight-pointer>,
then **IAM & Admin**:

- **Workload Identity Federation** lists the pool `kubespace-…` with the
  provider `kubespace-org`. Its condition names one subject only;
- **Service Accounts** in project P lists `kubespace-reader`, with no key;
- **IAM** on the scope lists `kubespace-reader` with the read roles.

## Back in Kubespace

Kubespace notices the access by itself and connects. To connect at once,
choose **Verify** on the setup card. Kubespace exchanges its own identity for a
short-lived token through your pool, reads with it, and stores no credential.

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>
