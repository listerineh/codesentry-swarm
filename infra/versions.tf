terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # Remote state with locking. Pass the bucket at init: terraform init -backend-config="bucket=<state-bucket>"
  backend "gcs" {
    prefix = "codesentry-swarm"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
