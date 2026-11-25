variable "org_subject" {
  description = "Unique ID of your organization's Kubespace Google service account. Kubespace gives it to you."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{1,64}$", var.org_subject))
    error_message = "org_subject is the numeric unique ID Kubespace gives you."
  }
}

variable "org_audience" {
  description = "Audience of your organization's Kubespace identity tokens. Kubespace gives it to you. The write role trusts this audience plus /eks-write."
  type        = string

  validation {
    condition     = can(regex("^https://[^ ]{1,240}$", var.org_audience))
    error_message = "org_audience is the https audience Kubespace gives you."
  }
}

variable "expected_account_id" {
  description = "The AWS account ID on the Kubespace setup card. The stack refuses to run in any other account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id is the 12-digit AWS account ID on the Kubespace setup card."
  }
}
