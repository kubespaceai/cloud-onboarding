variable "org_subject" {
  description = "Unique ID of your organization's Kubespace Google service account. Kubespace gives it to you."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{1,64}$", var.org_subject))
    error_message = "org_subject is the numeric unique ID Kubespace gives you."
  }
}

variable "org_audience" {
  description = "Audience of your organization's Kubespace identity tokens. Kubespace gives it to you."
  type        = string

  validation {
    condition     = can(regex("^https://[^ ]{1,240}$", var.org_audience))
    error_message = "org_audience is the https audience Kubespace gives you."
  }
}

variable "organizational_unit_ids" {
  description = "The organisation root (r-...) or the organisational units (ou-...) whose accounts Kubespace reads."
  type        = list(string)

  validation {
    condition = length(var.organizational_unit_ids) > 0 && alltrue([
      for id in var.organizational_unit_ids : can(regex("^(r-[0-9a-z]{4,32}|ou-[0-9a-z]{4,32}-[a-z0-9]{8,32})$", id))
    ])
    error_message = "Give at least one root (r-...) or organisational unit (ou-...) ID."
  }
}

variable "call_as" {
  description = "SELF in the management account, DELEGATED_ADMIN in a delegated StackSets administrator account."
  type        = string
  default     = "SELF"

  validation {
    condition     = contains(["SELF", "DELEGATED_ADMIN"], var.call_as)
    error_message = "call_as is SELF or DELEGATED_ADMIN."
  }
}

variable "display_name" {
  description = "Name Kubespace shows for the connection; each unit's ID is added to it."
  type        = string
  default     = "AWS organisation"
}
