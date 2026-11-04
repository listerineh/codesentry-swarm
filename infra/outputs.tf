output "webhook_url" {
  description = "Paste this in the repository's webhook settings."
  value       = "${google_cloud_run_v2_service.api.uri}/webhooks/github"
}

output "sandbox_job" {
  description = "Enable sandboxes on it after the first apply: gcloud beta run jobs update <job> --sandbox-launcher"
  value       = google_cloud_run_v2_job.sandbox.name
}
