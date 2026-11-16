# None of these values is a secret.

output "role_arn" {
  description = "The role Kubespace assumes in this account."
  value       = aws_iam_role.kubespace_read_only.arn
}

output "kubespace_setup" {
  description = "With units: one body per unit for POST /api/v1/cloud-org-scopes/aws/setup. Without: empty."
  value = [for id in var.organizational_unit_ids : {
    management_account_id  = data.aws_caller_identity.current.account_id
    organizational_unit_id = id
    display_name           = "${coalesce(var.display_name, "AWS organisation")} ${id}"
  }]
}

output "kubespace_account_setup" {
  description = "Without units: the body for POST /api/v1/cloud-connections/aws/setup. With units: null."
  value = length(var.organizational_unit_ids) > 0 ? null : {
    account_id   = data.aws_caller_identity.current.account_id
    display_name = coalesce(var.display_name, "AWS account ${data.aws_caller_identity.current.account_id}")
  }
}
