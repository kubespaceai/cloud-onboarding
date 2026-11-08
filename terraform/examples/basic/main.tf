# Root configuration that applies the submodules you need. Replace every value.

terraform {
  required_version = ">= 1.9, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0, < 7.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.0, < 5.0"
    }
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }
}

provider "aws" {
  region = "eu-west-1"
}

provider "azurerm" {
  features {}
  subscription_id = "00000000-0000-4000-8000-000000000002"
}

provider "google" {}

module "kubespace_aws" {
  source = "../../aws"

  org_subject             = "100000000000000000001"
  org_audience            = "https://identity.kubespace.ai/orgs/example"
  organizational_unit_ids = ["ou-ab12-11111111"]
}

module "kubespace_azure" {
  source = "../../azure"

  org_subject         = "100000000000000000001"
  management_group_id = "example-platform"
  location            = "westeurope"
}

module "kubespace_gcp" {
  source = "../../gcp"

  # A customer pool: Kubespace's setup gives each value.
  org_subject         = "100000000000000000001"
  pool_project_id     = "example-identity"
  pool_project_number = "123456789012"
  pool_id             = "kubespace-0123456789"

  scope_type       = "folder"
  scope_id         = "100000000000"
  quota_project_id = "example-billing"

  # Optional: the perimeter around the folder, if it has one.
  # vpc_sc_perimeter = "accessPolicies/123456789/servicePerimeters/example"
}

output "kubespace_setup" {
  description = "Send each body to Kubespace's setup call. None of them is a secret."
  value = {
    aws   = module.kubespace_aws.kubespace_setup
    azure = module.kubespace_azure.kubespace_setup
    gcp   = module.kubespace_gcp.kubespace_setup
  }
}
