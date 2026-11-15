variable "org_subject" {
  description = "Numeric Google subject of your organization's Kubespace identity. Kubespace gives it to you."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{21}$", var.org_subject))
    error_message = "org_subject is the 21-digit subject Kubespace gives you."
  }
}

variable "scope_type" {
  description = "Where Reader is assigned: management_group (every subscription under it), subscription or resource_group."
  type        = string
  default     = "management_group"

  validation {
    condition     = contains(["management_group", "subscription", "resource_group"], var.scope_type)
    error_message = "scope_type is management_group, subscription or resource_group."
  }
}

variable "management_group_id" {
  description = "scope_type = management_group: ID of the management group whose subscriptions Kubespace reads."
  type        = string
  default     = null

  validation {
    condition     = var.management_group_id == null || can(regex("^[A-Za-z0-9_.()-]{1,90}$", var.management_group_id))
    error_message = "management_group_id is the group's ID, not its display name or resource ID."
  }

  validation {
    condition     = (var.scope_type == "management_group") == (var.management_group_id != null)
    error_message = "Set management_group_id exactly when scope_type is management_group."
  }
}

variable "subscription_id" {
  description = "scope_type = subscription or resource_group: ID of the subscription Kubespace reads, or that holds the resource group."
  type        = string
  default     = null

  validation {
    condition     = var.subscription_id == null || can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id is the subscription's ID (a GUID), not its name."
  }

  validation {
    condition     = (var.scope_type != "management_group") == (var.subscription_id != null)
    error_message = "Set subscription_id exactly when scope_type is subscription or resource_group."
  }
}

variable "scope_resource_group" {
  description = "scope_type = resource_group: name of the resource group Kubespace reads."
  type        = string
  default     = null

  validation {
    condition     = var.scope_resource_group == null || can(regex("^[-A-Za-z0-9_.()]{0,89}[-A-Za-z0-9_()]$", var.scope_resource_group))
    error_message = "scope_resource_group is a resource group name."
  }

  validation {
    condition     = (var.scope_type == "resource_group") == (var.scope_resource_group != null)
    error_message = "Set scope_resource_group exactly when scope_type is resource_group."
  }
}

variable "create_resource_group" {
  description = "Create resource_group_name for the identity. Set false to put the identity in a resource group that already exists."
  type        = bool
  default     = true
}

variable "location" {
  description = "Azure region of the resource group this module creates for the identity. Not used with create_resource_group = false."
  type        = string
  default     = null

  validation {
    condition     = !var.create_resource_group || var.location != null
    error_message = "location is required unless create_resource_group is false."
  }
}

variable "identity_name" {
  description = "Name of the managed identity this module creates."
  type        = string
  default     = "kubespace-reader"
}

variable "resource_group_name" {
  description = "Resource group of the managed identity: created by this module, or an existing one with create_resource_group = false."
  type        = string
  default     = "rg-kubespace-reader"
}

variable "display_name" {
  description = "Name Kubespace shows for the connection. Defaults to the scope's kind."
  type        = string
  default     = null
}
