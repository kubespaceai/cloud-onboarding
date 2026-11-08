variable "org_service_account" {
  description = "Connections made before module 1.2: email of your organization's Kubespace Google service account, which gets the read roles. Leave it null and set org_subject, pool_project_id, pool_project_number and pool_id for a customer pool."
  type        = string
  default     = null

  validation {
    condition     = var.org_service_account == null || can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\\.iam\\.gserviceaccount\\.com$", var.org_service_account))
    error_message = "org_service_account is the service account email Kubespace gives you."
  }

  validation {
    condition     = (var.org_service_account == null) != (var.org_subject == null)
    error_message = "Set exactly one of org_service_account (the org's own service account) or org_subject (a pool in your organization)."
  }
}

# A customer pool (ADR-041): a Workload Identity pool and provider in a project P of your
# organization, and a kubespace-reader service account in P that only your Kubespace org's Google
# subject may impersonate. The read roles go to that reader, a principal of your organization.

variable "org_subject" {
  description = "Your Kubespace org's Google subject (numeric), from GET /api/v1/cloud-org-scopes/gcp/identity. The provider accepts this subject only."
  type        = string
  default     = null

  validation {
    condition     = var.org_subject == null || can(regex("^[0-9]{6,32}$", var.org_subject))
    error_message = "org_subject is the numeric subject Kubespace gives you."
  }

  validation {
    condition     = var.org_subject == null || (var.pool_project_id != null && var.pool_project_number != null && var.pool_id != null)
    error_message = "A customer pool needs pool_project_id, pool_project_number and pool_id."
  }
}

variable "pool_project_id" {
  description = "Project P in your organization that holds the pool, the provider and kubespace-reader."
  type        = string
  default     = null

  validation {
    condition     = var.pool_project_id == null || can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.pool_project_id))
    error_message = "pool_project_id is a project ID."
  }
}

variable "pool_project_number" {
  description = "Project number of pool_project_id."
  type        = string
  default     = null

  validation {
    condition     = var.pool_project_number == null || can(regex("^[0-9]{6,19}$", var.pool_project_number))
    error_message = "pool_project_number is the numeric project number."
  }
}

variable "pool_id" {
  description = "Workload Identity pool ID Kubespace gives you (kubespace-<10 hex>). One pool serves every scope of your Kubespace org."
  type        = string
  default     = null

  validation {
    condition     = var.pool_id == null || can(regex("^[a-z0-9-]{4,32}$", var.pool_id))
    error_message = "pool_id is the pool ID Kubespace gives you."
  }
}

variable "create_pool" {
  description = "Create the pool, provider and kubespace-reader in P. Set false in every module call but one when several scopes share the pool."
  type        = bool
  default     = true
}

variable "scope_type" {
  description = "project, folder or organization."
  type        = string

  validation {
    condition     = contains(["project", "folder", "organization"], var.scope_type)
    error_message = "scope_type is project, folder or organization."
  }

  validation {
    condition     = var.scope_type != "project" || var.org_subject != null
    error_message = "Project scope needs a customer pool (org_subject)."
  }
}

variable "scope_id" {
  description = "Project ID for a project scope; numeric folder or organization ID otherwise."
  type        = string

  validation {
    condition = (var.scope_type == "project"
      ? can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.scope_id))
    : can(regex("^[0-9]{1,20}$", var.scope_id)))
    error_message = "scope_id is the project ID for a project scope, or the numeric folder or organization ID."
  }
}

variable "quota_project_id" {
  description = "A project under the scope that Kubespace's read calls bill; its read APIs are enabled. Defaults to the project for a project scope."
  type        = string
  default     = null

  validation {
    condition     = var.quota_project_id == null || can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.quota_project_id))
    error_message = "quota_project_id is a project ID."
  }

  validation {
    condition     = var.quota_project_id != null || var.scope_type == "project"
    error_message = "A folder or organization scope needs quota_project_id."
  }
}

variable "display_name" {
  description = "Name Kubespace shows for the connection."
  type        = string
  default     = "Google Cloud folder"
}

variable "vpc_sc_perimeter" {
  description = "Optional. accessPolicies/<policy>/servicePerimeters/<name> of the perimeter around the scope. Adds an ingress rule that admits kubespace-reader only, to the read services only."
  type        = string
  default     = null

  validation {
    condition     = var.vpc_sc_perimeter == null || can(regex("^accessPolicies/[0-9]+/servicePerimeters/[A-Za-z0-9_]+$", var.vpc_sc_perimeter))
    error_message = "vpc_sc_perimeter is accessPolicies/<number>/servicePerimeters/<name>."
  }

  validation {
    condition     = var.vpc_sc_perimeter == null || var.org_subject != null
    error_message = "The ingress rule names kubespace-reader, so it needs a customer pool (org_subject)."
  }
}
