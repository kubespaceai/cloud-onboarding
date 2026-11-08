# Kubespace's read-only role in this account and, through a service-managed StackSet, in
# every account of the units you choose. The StackSet deploys role.yaml, the same template
# as a single-account connection. Accounts that join a unit get the role; accounts that
# leave lose it. No access key or user is created.

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  role_name = "KubespaceReadOnly"
  # The role trusts one Kubespace organization: AWS maps a Google service account token's
  # azp to accounts.google.com:aud and its aud to accounts.google.com:oaud.
  trust_policy = {
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = "accounts.google.com" }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "accounts.google.com:aud"  = var.org_subject
          "accounts.google.com:sub"  = var.org_subject
          "accounts.google.com:oaud" = var.org_audience
        }
      }
    }]
  }
  read_policies = [
    "arn:aws:iam::aws:policy/SecurityAudit",
    "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess",
  ]
}

# In this account the role lets Kubespace list the accounts of the units.
resource "aws_iam_role" "kubespace_read_only" {
  name               = local.role_name
  assume_role_policy = jsonencode(local.trust_policy)
}

resource "aws_iam_role_policy_attachment" "kubespace_read" {
  for_each = toset(local.read_policies)

  role       = aws_iam_role.kubespace_read_only.name
  policy_arn = each.value
}

resource "aws_cloudformation_stack_set" "kubespace_read_only" {
  name             = "Kubespace-ReadOnly"
  description      = "Kubespace's read-only role, from role.yaml, in every account of the chosen units."
  permission_model = "SERVICE_MANAGED"
  call_as          = var.call_as
  capabilities     = ["CAPABILITY_NAMED_IAM"]
  template_body    = file("${path.module}/role.yaml")

  parameters = {
    OrgSubject  = var.org_subject
    OrgAudience = var.org_audience
  }

  auto_deployment {
    enabled                          = true
    retain_stacks_on_account_removal = false
  }

  managed_execution {
    active = true
  }

  operation_preferences {
    max_concurrent_percentage    = 25
    failure_tolerance_percentage = 10
    region_concurrency_type      = "PARALLEL"
  }

  lifecycle {
    # AWS sets this for service-managed StackSets.
    ignore_changes = [administration_role_arn]
  }
}

resource "aws_cloudformation_stack_set_instance" "units" {
  stack_set_name            = aws_cloudformation_stack_set.kubespace_read_only.name
  call_as                   = var.call_as
  stack_set_instance_region = data.aws_region.current.region

  deployment_targets {
    organizational_unit_ids = var.organizational_unit_ids
  }
}
