# A user-assigned managed identity that you own, a federated credential that trusts only your
# Kubespace organization's Google subject, and Reader at one scope: a management group (every
# subscription under it, now or later), a subscription, or a resource group. The provider's
# subscription holds the identity. No secret exists.

locals {
  scope = {
    management_group = "/providers/Microsoft.Management/managementGroups/${coalesce(var.management_group_id, "-")}"
    subscription     = "/subscriptions/${coalesce(var.subscription_id, "-")}"
    resource_group   = "/subscriptions/${coalesce(var.subscription_id, "-")}/resourceGroups/${coalesce(var.scope_resource_group, "-")}"
  }[var.scope_type]

  display_name = coalesce(var.display_name, {
    management_group = "Azure management group"
    subscription     = "Azure subscription"
    resource_group   = "Azure resource group"
  }[var.scope_type])

  identity_resource_group = var.create_resource_group ? azurerm_resource_group.kubespace[0] : data.azurerm_resource_group.kubespace[0]
}

resource "azurerm_resource_group" "kubespace" {
  count = var.create_resource_group ? 1 : 0

  name     = var.resource_group_name
  location = var.location
}

# create_resource_group = false: the identity goes in a resource group that already exists, for
# example the resource group a resource_group scope reads.
data "azurerm_resource_group" "kubespace" {
  count = var.create_resource_group ? 0 : 1

  name = var.resource_group_name
}

resource "azurerm_user_assigned_identity" "reader" {
  name                = var.identity_name
  resource_group_name = local.identity_resource_group.name
  location            = local.identity_resource_group.location
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
