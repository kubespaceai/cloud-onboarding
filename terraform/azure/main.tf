# The same managed identity and federated credential as Kubespace's single-subscription
# template, with Reader on a management group, so every subscription under the group,
# now or later, reads. The provider's subscription holds the identity. No secret exists:
# the credential trusts one Google subject.

locals {
  scope = "/providers/Microsoft.Management/managementGroups/${var.management_group_id}"
}

resource "azurerm_resource_group" "kubespace" {
  name     = var.resource_group_name
  location = var.location
}

resource "azurerm_user_assigned_identity" "reader" {
  name                = var.identity_name
  resource_group_name = azurerm_resource_group.kubespace.name
  location            = azurerm_resource_group.kubespace.location
}

resource "azurerm_federated_identity_credential" "kubespace" {
  name                      = "kubespace"
  user_assigned_identity_id = azurerm_user_assigned_identity.reader.id
  issuer                    = "https://accounts.google.com"
  subject                   = var.org_subject
  audience                  = ["api://AzureADTokenExchange"]
}

resource "azurerm_role_assignment" "reader" {
  scope                = local.scope
  role_definition_name = "Reader"
  principal_id         = azurerm_user_assigned_identity.reader.principal_id
  principal_type       = "ServicePrincipal"
}
