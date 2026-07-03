resource "google_cloud_run_v2_service" "hs_dvrt_plot" {
  name                = "hs-dvrt-plot-${var.environment}"
  location            = var.gcp_region
  deletion_protection = false

  template {
    service_account = google_service_account.hs_dvrt_plot_cloud_run_service_account.email

    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      resources {
        limits = {
          cpu    = var.cloud_run_web_cpu
          memory = var.cloud_run_web_memory
        }
      }

      ports {
        container_port = 55620
      }
    }

    scaling {
      min_instance_count = var.cloud_run_scaling_min_instance
      max_instance_count = var.cloud_run_scaling_max_instance
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}

resource "google_cloud_run_service_iam_member" "hs_dvrt_plot_public_access" {
  count    = var.public_access_enabled ? 1 : 0
  location = google_cloud_run_v2_service.hs_dvrt_plot.location
  service  = google_cloud_run_v2_service.hs_dvrt_plot.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}


# ---------------------------------
# GCP Cloud Run Service Account
# ---------------------------------

resource "google_service_account" "hs_dvrt_plot_cloud_run_service_account" {
  account_id   = "hs-dvrt-plot-cloud-run-${var.environment}"
  display_name = "HydroServer DVRT Plot Cloud Run Service Account — ${var.environment}"
  project      = data.google_project.current.project_id
}

resource "google_cloud_run_v2_service_iam_member" "hs_dvrt_plot_cloud_run_invoker" {
  project  = data.google_project.current.project_id
  location = google_cloud_run_v2_service.hs_dvrt_plot.location
  name     = google_cloud_run_v2_service.hs_dvrt_plot.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.hs_dvrt_plot_cloud_run_service_account.email}"
}
