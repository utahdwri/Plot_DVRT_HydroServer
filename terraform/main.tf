terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  backend "gcs" {}
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

data "google_project" "current" {
  project_id = var.gcp_project_id
}

data "google_client_config" "current" {}


# ---------------------------------
# GCP Environment Configuration
# ---------------------------------

variable "environment" {
  description = "The name of the instance environment (prod, test, dev)"
  type        = string
}

variable "gcp_project_id" {
  description = "The project ID for this instance."
  type        = string
}

variable "gcp_region" {
  description = "The GCP region this instance will be deployed in."
  type        = string
}

variable "public_access_enabled" {
  description = "Controls whether GCP services will be publicly accessible."
  type        = bool
  default     = false
}

variable "cloud_run_web_cpu" {
  description = "The number of vCPUs to assign to each web service container."
  type        = string
  default     = "1"
}

variable "cloud_run_web_memory" {
  description = "The amount of memory to assign to each web service container."
  type        = string
  default     = "2Gi"
}

variable "cloud_run_scaling_min_instance" {
  description = "The minimum number of web service containers Cloud Run will run."
  type        = number
  default     = 1
}

variable "cloud_run_scaling_max_instance" {
  description = "The maximum number of web service containers Cloud Run will run."
  type        = number
  default     = 1
}


# ---------------------------------
# GCP Outputs
# ---------------------------------

output "cloud_run_url" {
  description = "Public URL of the deployed Cloud Run service."
  value       = google_cloud_run_v2_service.hs_dvrt_plot.uri
}

output "artifact_registry_repository" {
  description = "Full image path prefix for use in the GitHub Actions build/push/deploy steps."
  value       = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${google_artifact_registry_repository.hs_dvrt_plot.repository_id}"
}
