variable "org_service_account" {
  description = "Email of your organization's Kubespace Google service account. Kubespace gives it to you."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\\.iam\\.gserviceaccount\\.com$", var.org_service_account))
    error_message = "org_service_account is the service account email Kubespace gives you."
  }
}

variable "scope_type" {
  description = "folder or organization."
  type        = string

  validation {
    condition     = contains(["folder", "organization"], var.scope_type)
    error_message = "scope_type is folder or organization."
  }
}

variable "scope_id" {
  description = "Numeric ID of the folder or organization whose projects Kubespace reads."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{1,20}$", var.scope_id))
    error_message = "scope_id is the numeric folder or organization ID."
  }
}

variable "quota_project_id" {
  description = "A project under the scope that Kubespace's read calls bill. Its Cloud Resource Manager API is enabled."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.quota_project_id))
    error_message = "quota_project_id is a project ID."
  }
}

variable "display_name" {
  description = "Name Kubespace shows for the connection."
  type        = string
  default     = "Google Cloud folder"
}
