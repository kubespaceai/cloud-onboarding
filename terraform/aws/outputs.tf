# None of these values is a secret.

output "role_arn" {
  description = "The role Kubespace assumes in this account to list the units' accounts."
  value       = aws_iam_role.kubespace_read_only.arn
}

output "kubespace_setup" {
  description = "One body per unit for POST /api/v1/cloud-org-scopes/aws/setup."
  value = [for id in var.organizational_unit_ids : {
    management_account_id  = data.aws_caller_identity.current.account_id
    organizational_unit_id = id
    display_name           = "${var.display_name} ${id}"
  }]
}
