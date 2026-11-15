# None of these values is a secret.

output "kubespace_setup" {
  description = "The setup body for Kubespace: POST /api/v1/cloud-org-scopes/azure/setup for a management group, POST /api/v1/cloud-connections/azure/setup for a subscription or resource group."
  value = { for name, value in {
    directory_id        = azurerm_user_assigned_identity.reader.tenant_id
    client_id           = azurerm_user_assigned_identity.reader.client_id
    management_group_id = var.management_group_id
    subscription_id     = var.subscription_id
    scope_type          = var.scope_type == "management_group" ? null : var.scope_type
    resource_group      = var.scope_resource_group
    display_name        = local.display_name
  } : name => value if value != null }
}

output "reader_scope" {
  description = "The scope of the Reader role assignment."
  value       = local.scope
}
