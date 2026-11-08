# None of these values is a secret.

output "kubespace_setup" {
  description = "For a folder or organization: the body for POST /api/v1/cloud-org-scopes/gcp/setup. For a project: project_id and display_name for POST /api/v1/cloud-connections/gcp/setup."
  # Only the keys that apply; a folder or organization scope without a pool is the 1.x body.
  value = { for k, v in {
    project_id          = var.scope_type == "project" ? var.scope_id : null
    scope_type          = var.scope_type == "project" ? null : var.scope_type
    scope_id            = var.scope_type == "project" ? null : var.scope_id
    quota_project_id    = var.scope_type == "project" ? null : var.quota_project_id
    display_name        = var.display_name
    pool_project_id     = var.pool_project_id
    pool_project_number = var.pool_project_number
  } : k => v if v != null }
  depends_on = [
    google_project_iam_member.kubespace,
    google_folder_iam_member.kubespace,
    google_organization_iam_member.kubespace,
    google_service_account_iam_member.impersonation,
  ]
}

output "reader_service_account" {
  description = "The kubespace-reader service account that holds the read roles; null without a customer pool."
  value       = local.reader_email
}

output "trust" {
  description = "The customer pool objects, as Kubespace's setup card lists them; null without a customer pool."
  value = local.customer_pool ? {
    project_id             = var.pool_project_id
    project_number         = var.pool_project_number
    pool_id                = var.pool_id
    provider_id            = "kubespace-org"
    issuer                 = "https://accounts.google.com"
    attribute_condition    = "assertion.sub == '${var.org_subject}'"
    reader_service_account = local.reader_email
    impersonation_member   = local.impersonation_member
  } : null
}
