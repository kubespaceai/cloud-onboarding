# Kubespace cloud onboarding

This repository contains what a cloud administrator runs to connect an
account to [Kubespace](https://kubespace.ai) without giving Kubespace a key.
Everything here runs under **your** identity, in **your** environment.

Do not use `main` for onboarding. Use the release that Kubespace links to.

| Part | Status |
| --- | --- |
| `terraform/` | Released from `v1.1.0` |
| `gcp/` | Not released yet |

## What lives here

| Path | Contents |
| --- | --- |
| `gcp/` | Google Cloud: Cloud Shell tutorial, read-only pre-flight script, apply script |
| `terraform/` | Onboarding Terraform modules for Google Cloud, AWS and Azure |

The step-by-step guides are on [docs.kubespace.ai](https://docs.kubespace.ai).

## How Kubespace uses it

- **Cloud Shell.** The Kubespace app opens this repository in Google Cloud
  Shell with a guided tutorial. Cloud Shell opens repositories that Google
  doesn't own in a temporary session with no access to your credentials. The
  tutorial therefore asks you to sign in with `gcloud auth login`, and the
  session is deleted when it ends.
- **Terraform.** Reference a module with a pinned release, for example
  `source = "git::https://github.com/kubespaceai/cloud-onboarding.git//terraform/gcp?ref=<release>"`.
- **Pre-flight.** The pre-flight script only reads. It prints the exact data it
  would send to Kubespace and asks before sending anything. It never sends
  credentials or tokens.

## Pinning and verification

- Releases are cut on `release-x.y` branches and `vX.Y.Z` tags. Once created,
  they can't be changed or deleted.
- Kubespace links always name a release. The first step of each tutorial checks
  that the commit you cloned matches the one Kubespace shows you.

## Security

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## License

[Apache License 2.0](LICENSE).
