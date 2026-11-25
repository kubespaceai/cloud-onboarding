# None of these values is a secret.

output "role_arn" {
  description = "The EKS write role Kubespace assumes in this account."
  value       = aws_cloudformation_stack.kubespace_eks_write.outputs["RoleArn"]
}

output "capability_role_arn" {
  description = "The role the EKS Capability for Argo CD runs as. It has no permissions of its own."
  value       = aws_cloudformation_stack.kubespace_eks_write.outputs["CapabilityRoleArn"]
}
