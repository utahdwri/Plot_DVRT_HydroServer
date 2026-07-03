# ---------------------------------
# GCP Artifact Registry
# ---------------------------------


resource "google_artifact_registry_repository" "hs_dvrt_plot" {
  project       = var.gcp_project_id
  location      = var.gcp_region
  repository_id = "hs-dvrt-plot-${var.environment}"
  format        = "DOCKER"
  description   = "Container images for the hs-dvrt-plot-${var.environment} Cloud Run service."

  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "86400s"
    }
  }
}
