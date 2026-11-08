# None of these values is a secret.

output "kubespace_setup" {
  description = "The body for POST /api/v1/cloud-org-scopes/azure/setup."
  value = {
    directory_id        = azurerm_user_assigned_identity.reader.tenant_id
    client_id           = azurerm_user_assigned_identity.reader.client_id
    management_group_id = var.management_group_id
    display_name        = var.display_name
  }
}
