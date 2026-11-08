# The read roles of a single-project connection, granted once on a folder or organization
# so every project under it, now or later, reads, plus roles/browser so Kubespace can list
# those projects. Grants are additive members: no existing binding changes. No key exists.

locals {
  roles = [
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
    "roles/browser",
  ]
  member = "serviceAccount:${var.org_service_account}"
}

resource "google_folder_iam_member" "kubespace" {
  for_each = var.scope_type == "folder" ? toset(local.roles) : toset([])

  folder = "folders/${var.scope_id}"
  role   = each.value
  member = local.member
}

resource "google_organization_iam_member" "kubespace" {
  for_each = var.scope_type == "organization" ? toset(local.roles) : toset([])

  org_id = var.scope_id
  role   = each.value
  member = local.member
}

resource "google_project_service" "resource_manager" {
  project            = var.quota_project_id
  service            = "cloudresourcemanager.googleapis.com"
  disable_on_destroy = false
}

# Discovery reads effective org policies (Domain Restricted Sharing and others) through this API.
resource "google_project_service" "org_policy" {
  project            = var.quota_project_id
  service            = "orgpolicy.googleapis.com"
  disable_on_destroy = false
}
