# Kubespace's EKS write role KubespaceEksWrite (and the Argo CD capability role it passes to EKS) in this
# account: eks-write-role.yaml as a CloudFormation stack, the same template as the console quick-create link
# on the Kubespace setup card. Kubespace assumes the write role only from Credential Manager, for approved
# plan steps on clusters tagged kubespace.ai/managed=true. No access key is created. It adds the write role
# only: KubespaceReadOnly is not touched. Destroy this module call to revoke the role.

resource "aws_cloudformation_stack" "kubespace_eks_write" {
  name          = "Kubespace-EksWrite"
  capabilities  = ["CAPABILITY_NAMED_IAM"]
  template_body = file("${path.module}/eks-write-role.yaml")

  parameters = {
    OrgSubject        = var.org_subject
    OrgAudience       = var.org_audience
    ExpectedAccountId = var.expected_account_id
  }
}
