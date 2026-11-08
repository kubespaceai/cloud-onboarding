variable "org_subject" {
  description = "Numeric Google subject of your organization's Kubespace identity. Kubespace gives it to you."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{21}$", var.org_subject))
    error_message = "org_subject is the 21-digit subject Kubespace gives you."
  }
}

variable "management_group_id" {
  description = "ID of the management group whose subscriptions Kubespace reads."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.()-]{1,90}$", var.management_group_id))
    error_message = "management_group_id is the group's ID, not its display name or resource ID."
  }
}

variable "location" {
  description = "Azure region of the managed identity's resource group."
  type        = string
}

variable "identity_name" {
  description = "Name of the managed identity this module creates."
  type        = string
  default     = "kubespace-reader"
}

variable "resource_group_name" {
  description = "Resource group this module creates for the managed identity."
  type        = string
  default     = "rg-kubespace-reader"
}

variable "display_name" {
  description = "Name Kubespace shows for the connection."
  type        = string
  default     = "Azure management group"
}
