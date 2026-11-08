# Kubespace read-only pre-flight

## Before you start

<walkthrough-tutorial-duration duration="5"></walkthrough-tutorial-duration>

This pre-flight shows Kubespace your Google Cloud estate **before** you grant
anything. It runs under your own identity and only reads. It prints the exact
JSON it would send, then asks you before sending it.

It never sends a key, a password or a token. Kubespace gets only the summary
and your single-use pairing code.

You need:

- the command from the Kubespace setup card, with your pairing code. The code
  works once and for 15 minutes;
- read access to the scope: for example **Folder Viewer** or **Organization
  Viewer**, **Organization Policy Viewer**, **Access Context Manager Reader**,
  **Kubernetes Engine Cluster Viewer** and **Service Usage Viewer**. Whatever
  you can't read is reported as unknown, never as absent.

**Behind VPC Service Controls?** Cloud Shell is outside every perimeter, so a
perimeter can refuse its reads of protected services. The pre-flight reports
that as `vpc_sc_blocked`. To read inside the perimeter, run the same command
from a workstation or VM with `gcloud` that is inside the perimeter's access
level. See the README in this folder.

## Sign in

Cloud Shell opens this repository in a temporary session with no access to
your credentials. Sign in with your own account:

```bash
gcloud auth login
```

Check the commit you are running. Compare it with the commit Kubespace names,
or with `main` on GitHub until `gcp/` is released:

```bash
git rev-parse HEAD
```

## Run the pre-flight

Paste the command from the Kubespace setup card. It names your scope, the
Kubespace address and your pairing code. Here, in the repository Cloud Shell
opened, it comes down to:

```bash
bash gcp/preflight.sh --scope folders/123456789012 --endpoint https://app.kubespace.ai/api/v1/cloud-org-scopes/gcp/preflight --code ksp_XXXXXXXXXXXXXXXXXXXXXXXXXX
```

To see the summary without sending anything, add `--dry-run`.

## Review and send

The pre-flight prints the exact JSON. Read it. Type `yes` to send it, or
anything else to send nothing.

Back in Kubespace, the agent proposes the scope, the roles and how to apply
them, and names any blockers (Domain Restricted Sharing, VPC Service Controls,
disabled APIs) before you grant anything.

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>
