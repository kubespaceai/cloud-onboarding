# The read roles of a single-project connection, granted on a project, or once on a folder or
# organization so every project under it, now or later, reads (plus roles/browser so Kubespace can
# list those projects). Grants are additive members: no existing binding changes. No key exists.
#
# The grantee is kubespace-reader, a service account in your organization that only your Kubespace
# org's Google subject may impersonate through a Workload Identity pool (customer pool), or, for
# connections made before module 1.2, your Kubespace org's own service account.

locals {
  read_roles = [
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
  ]
  # ADR-045: four optional feature groups, each of them all its roles or none. The other four roles
  # (Cloud Asset, Compute network, Kubernetes Engine and Service Usage) are the core, always granted.
  feature_group_roles = {
    supply_chain = [
      "roles/artifactregistry.reader",
      "roles/binaryauthorization.policyViewer",
      "roles/cloudbuild.builds.viewer",
      "roles/clouddeploy.viewer",
    ]
    fleet             = ["roles/gkehub.viewer"]
    governance        = ["roles/orgpolicy.policyViewer"]
    secrets_inventory = ["roles/secretmanager.viewer"]
  }
  # The APIs only a group's roles probe: a group left out enables none of them.
  feature_group_apis = {
    supply_chain = [
      "artifactregistry.googleapis.com",
      "binaryauthorization.googleapis.com",
      "cloudbuild.googleapis.com",
      "clouddeploy.googleapis.com",
    ]
    fleet             = ["gkehub.googleapis.com"]
    governance        = ["orgpolicy.googleapis.com"]
    secrets_inventory = ["secretmanager.googleapis.com"]
  }
  # PRO-3577: the opt-in groups, off unless listed in feature_groups. Never in the default, and their
  # roles and APIs are not in read_roles or read_apis.
  opt_in_group_roles = {
    audit_log = ["roles/logging.viewer"]
  }
  opt_in_group_apis = {
    audit_log = ["logging.googleapis.com"]
  }
  left_out       = [for group in keys(local.feature_group_roles) : group if !contains(var.feature_groups, group)]
  left_out_roles = flatten([for group in local.left_out : local.feature_group_roles[group]])
  left_out_apis  = flatten([for group in local.left_out : local.feature_group_apis[group]])
  opted_in       = [for group in keys(local.opt_in_group_roles) : group if contains(var.feature_groups, group)]
  opt_in_roles   = flatten([for group in local.opted_in : local.opt_in_group_roles[group]])
  opt_in_apis    = flatten([for group in local.opted_in : local.opt_in_group_apis[group]])
  granted_roles  = concat([for role in local.read_roles : role if !contains(local.left_out_roles, role)], local.opt_in_roles)

  roles = var.scope_type == "project" ? local.granted_roles : concat(local.granted_roles, ["roles/browser"])

  # The APIs Kubespace's read calls use, enabled on the quota project. Cloud Resource Manager and
  # Org Policy have their own resources (module 1.0 and 1.1), so their addresses stay the same.
  read_apis = [
    "artifactregistry.googleapis.com",
    "binaryauthorization.googleapis.com",
    "cloudasset.googleapis.com",
    "cloudbuild.googleapis.com",
    "clouddeploy.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "gkehub.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
  ]
  quota_project = coalesce(var.quota_project_id, var.scope_id)

  customer_pool = var.org_subject != null
  # What P needs for the token exchange and the impersonation.
  pool_apis = local.customer_pool && var.create_pool ? toset([
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ]) : toset([])

  reader_email = local.customer_pool ? "kubespace-reader@${var.pool_project_id}.iam.gserviceaccount.com" : null
  # One subject, never principalSet://.../*.
  impersonation_member = local.customer_pool ? join("", [
    "principal://iam.googleapis.com/projects/${var.pool_project_number}/locations/global/",
    "workloadIdentityPools/${var.pool_id}/subject/${var.org_subject}",
  ]) : null

  member = local.customer_pool ? "serviceAccount:${local.reader_email}" : "serviceAccount:${var.org_service_account}"

  # The services the VPC Service Controls ingress rule admits kubespace-reader to.
  vpc_sc_services = [
    "cloudasset.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "serviceusage.googleapis.com",
  ]
}

# --- Customer pool in project P -------------------------------------------------------------

resource "google_project_service" "pool" {
  for_each = local.pool_apis

  project            = var.pool_project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "kubespace" {
  count = local.customer_pool && var.create_pool ? 1 : 0

  project                   = var.pool_project_id
  workload_identity_pool_id = var.pool_id
  display_name              = "Kubespace"

  depends_on = [google_project_service.pool]
}

resource "google_iam_workload_identity_pool_provider" "kubespace" {
  count = local.customer_pool && var.create_pool ? 1 : 0

  project                            = var.pool_project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.kubespace[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "kubespace-org"
  display_name                       = "Kubespace org"
  attribute_mapping                  = { "google.subject" = "assertion.sub" }
  # Always set: without it the provider would accept any Google identity's token.
  attribute_condition = "assertion.sub == '${var.org_subject}'"

  oidc {
    issuer_uri = "https://accounts.google.com"
  }
}

resource "google_service_account" "reader" {
  count = local.customer_pool && var.create_pool ? 1 : 0

  project      = var.pool_project_id
  account_id   = "kubespace-reader"
  display_name = "Kubespace reader"
  description  = "Read-only access for Kubespace. Only your Kubespace org's subject may impersonate it."

  depends_on = [google_project_service.pool]
}

resource "google_service_account_iam_member" "impersonation" {
  count = local.customer_pool && var.create_pool ? 1 : 0

  service_account_id = "projects/${var.pool_project_id}/serviceAccounts/${local.reader_email}"
  role               = "roles/iam.workloadIdentityUser"
  member             = local.impersonation_member

  depends_on = [google_service_account.reader, google_iam_workload_identity_pool_provider.kubespace]
}

# --- Read roles on the scope ----------------------------------------------------------------

resource "google_project_iam_member" "kubespace" {
  for_each = var.scope_type == "project" ? toset(local.roles) : toset([])

  project = var.scope_id
  role    = each.value
  member  = local.member

  depends_on = [google_service_account.reader]
}

resource "google_folder_iam_member" "kubespace" {
  for_each = var.scope_type == "folder" ? toset(local.roles) : toset([])

  folder = "folders/${var.scope_id}"
  role   = each.value
  member = local.member

  depends_on = [google_service_account.reader]
}

resource "google_organization_iam_member" "kubespace" {
  for_each = var.scope_type == "organization" ? toset(local.roles) : toset([])

  org_id = var.scope_id
  role   = each.value
  member = local.member

  depends_on = [google_service_account.reader]
}

# --- APIs on the quota project --------------------------------------------------------------

resource "google_project_service" "resource_manager" {
  project            = local.quota_project
  service            = "cloudresourcemanager.googleapis.com"
  disable_on_destroy = false
}

# Discovery reads effective org policies (Domain Restricted Sharing and others) through this API.
# Only with the governance group: its one role is the only one that probes it.
resource "google_project_service" "org_policy" {
  count = contains(local.left_out_apis, "orgpolicy.googleapis.com") ? 0 : 1

  project            = local.quota_project
  service            = "orgpolicy.googleapis.com"
  disable_on_destroy = false
}

# Module 1.8 counts this resource; a call made before keeps its address and changes nothing.
moved {
  from = google_project_service.org_policy
  to   = google_project_service.org_policy[0]
}

resource "google_project_service" "read" {
  for_each = toset(concat([for api in local.read_apis : api if !contains(local.left_out_apis, api)], local.opt_in_apis))

  project            = local.quota_project
  service            = each.value
  disable_on_destroy = false
}

# --- Optional VPC Service Controls ingress rule ---------------------------------------------

# Admits kubespace-reader only, to the read services only. The token exchange and impersonation
# use STS and the Service Account Credentials API, which a perimeter does not restrict. The source
# is any access level: Kubespace's egress addresses are not fixed, so the identity alone admits.
# If your perimeter's ingress policies are managed inline elsewhere, add this rule there instead.
resource "google_access_context_manager_service_perimeter_ingress_policy" "kubespace" {
  count = var.vpc_sc_perimeter == null ? 0 : 1

  perimeter = var.vpc_sc_perimeter

  ingress_from {
    identities = [local.member]
    sources {
      access_level = "*"
    }
  }

  ingress_to {
    resources = ["*"]

    dynamic "operations" {
      for_each = local.vpc_sc_services
      content {
        service_name = operations.value
        method_selectors {
          method = "*"
        }
      }
    }
  }
}
