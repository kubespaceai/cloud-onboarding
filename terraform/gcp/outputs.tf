# None of these values is a secret.

output "kubespace_setup" {
  description = "The body for POST /api/v1/cloud-org-scopes/gcp/setup."
  value = {
    scope_type       = var.scope_type
    scope_id         = var.scope_id
    quota_project_id = var.quota_project_id
    display_name     = var.display_name
  }
  depends_on = [google_folder_iam_member.kubespace, google_organization_iam_member.kubespace]
}
