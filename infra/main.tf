locals {
  stages = toset(["triage", "run", "judge"])
  apis = toset([
    "aiplatform.googleapis.com",
    "eventarc.googleapis.com",
    "firestore.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "telemetry.googleapis.com",
  ])
  secrets = toset(["webhook-secret", "api-token", "github-token"])
}

data "google_project" "this" {}

# Reading this also creates the agent, which a new project doesn't have until first use.
data "google_storage_project_service_account" "gcs" {}

resource "google_project_service" "apis" {
  for_each           = local.apis
  service            = each.value
  disable_on_destroy = false
}

# ── Identities ───────────────────────────────────────────────────────────────────

resource "google_service_account" "api" {
  account_id = "codesentry-api"
}

resource "google_service_account" "pusher" {
  account_id = "codesentry-pusher"
}

# Deliberately gets no roles: the job only holds signed URLs.
resource "google_service_account" "sandbox" {
  account_id = "codesentry-sandbox"
}

resource "google_project_iam_member" "api" {
  for_each = toset(["roles/aiplatform.user", "roles/datastore.user", "roles/telemetry.tracesWriter"])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.api.member
}

# Signed URLs are signed with the API's own identity.
resource "google_service_account_iam_member" "api_signs_as_itself" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.api.member
}

resource "google_service_account_iam_member" "api_acts_as_sandbox" {
  service_account_id = google_service_account.sandbox.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.api.member
}

# ── State and files ──────────────────────────────────────────────────────────────

resource "google_firestore_database" "default" {
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}

resource "google_storage_bucket" "sandbox" {
  name                        = "${var.project_id}-codesentry-sandbox"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "api" {
  bucket = google_storage_bucket.sandbox.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.api.member
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = local.secrets
  secret_id = "codesentry-${each.value}"

  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "api" {
  for_each  = local.secrets
  secret_id = google_secret_manager_secret.secrets[each.value].id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.api.member
}

# ── Events ───────────────────────────────────────────────────────────────────────

resource "google_pubsub_topic" "stage" {
  for_each   = local.stages
  name       = "codesentry-${each.value}"
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic" "dead_letter" {
  name       = "codesentry-dead-letter"
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_subscription" "dead_letter" {
  name                       = "codesentry-dead-letter"
  topic                      = google_pubsub_topic.dead_letter.id
  message_retention_duration = "604800s"
}

resource "google_pubsub_topic_iam_member" "api_publishes" {
  for_each = local.stages
  topic    = google_pubsub_topic.stage[each.value].name
  role     = "roles/pubsub.publisher"
  member   = google_service_account.api.member
}

resource "google_pubsub_subscription" "stage" {
  for_each             = local.stages
  name                 = "codesentry-${each.value}"
  topic                = google_pubsub_topic.stage[each.value].id
  ack_deadline_seconds = 600 # the maximum, and the request timeout for push

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.api.uri}/internal/pubsub"

    oidc_token {
      service_account_email = google_service_account.pusher.email
      audience              = google_cloud_run_v2_service.api.uri
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = var.max_delivery_attempts
  }
}

# Pub/Sub moves messages to the dead letter topic with its own service agent.
locals {
  pubsub_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  topic  = google_pubsub_topic.dead_letter.name
  role   = "roles/pubsub.publisher"
  member = local.pubsub_agent
}

resource "google_pubsub_subscription_iam_member" "dead_letter_subscriber" {
  for_each     = local.stages
  subscription = google_pubsub_subscription.stage[each.value].name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}

resource "google_project_iam_member" "gcs_publishes_events" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = data.google_storage_project_service_account.gcs.member
}

resource "google_project_iam_member" "pusher_receives_events" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = google_service_account.pusher.member
}

resource "google_eventarc_trigger" "sandbox_result" {
  name            = "codesentry-sandbox-result"
  location        = var.region
  service_account = google_service_account.pusher.email

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }

  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.sandbox.name
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.api.name
      region  = var.region
      path    = "/internal/sandbox-result"
    }
  }

  depends_on = [google_project_iam_member.gcs_publishes_events, google_project_iam_member.pusher_receives_events]
}

# ── Compute ──────────────────────────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "api" {
  name     = "codesentry-api"
  location = var.region
  # GitHub must reach the webhook. /internal/* checks OIDC and /tasks a bearer token in the app.
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.api.email
    timeout         = "600s"

    scaling {
      max_instance_count = 5
    }

    containers {
      image = var.api_image

      dynamic "env" {
        for_each = {
          CODESENTRY_MODE                   = "gcp"
          CODESENTRY_LLM                    = "gemini"
          CODESENTRY_SANDBOX                = "cloud-run-job"
          CODESENTRY_PROJECT_ID             = var.project_id
          CODESENTRY_BUCKET                 = google_storage_bucket.sandbox.name
          CODESENTRY_JOB_REGION             = var.region
          CODESENTRY_SIGNER_SERVICE_ACCOUNT = google_service_account.api.email
          CODESENTRY_PUSH_SERVICE_ACCOUNT   = google_service_account.pusher.email
          CODESENTRY_PUSH_AUDIENCE          = "https://codesentry-api-"
        }
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = {
          CODESENTRY_WEBHOOK_SECRET = "webhook-secret"
          CODESENTRY_API_TOKEN      = "api-token"
          CODESENTRY_GITHUB_TOKEN   = "github-token"
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secrets[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis, google_secret_manager_secret_iam_member.api]
}

resource "google_cloud_run_v2_service_iam_member" "public_webhook" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_job" "sandbox" {
  name                = "codesentry-sandbox"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.sandbox.email
      timeout         = "300s"
      max_retries     = 0

      containers {
        image   = var.sandbox_image
        command = ["python3", "/app/job_main.py"]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# Runs with overrides, which is what the API does for each iteration.
resource "google_cloud_run_v2_job_iam_member" "api_runs_sandbox" {
  name     = google_cloud_run_v2_job.sandbox.name
  location = var.region
  role     = "roles/run.developer"
  member   = google_service_account.api.member
}

# ── Observability ────────────────────────────────────────────────────────────────

resource "google_monitoring_dashboard" "swarm" {
  dashboard_json = file("${path.module}/dashboard.json")
  depends_on     = [google_project_service.apis]
}
