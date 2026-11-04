variable "project_id" {
  description = "Google Cloud project that hosts the swarm."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, the bucket and Eventarc."
  type        = string
  default     = "us-central1"
}

variable "api_image" {
  description = "Pinned image of the API (Dockerfile)."
  type        = string
}

variable "sandbox_image" {
  description = "Pinned image of the sandbox (Dockerfile.sandbox)."
  type        = string
}

variable "max_delivery_attempts" {
  description = "Deliveries before a message goes to the dead letter topic."
  type        = number
  default     = 5
}
