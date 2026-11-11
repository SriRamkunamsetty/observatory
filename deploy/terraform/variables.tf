variable "environment" {
  description = "Target deployment environment (prod, staging, dev)"
  type        = string
  default     = "prod"
}

variable "namespace" {
  description = "Kubernetes namespace for Chat Observatory"
  type        = string
  default     = "observatory"
}

variable "api_key" {
  description = "Authentication key for OpenTelemetry trace ingestion (X-API-Key)"
  type        = string
  sensitive   = true
}

variable "secret_key" {
  description = "Flask session secret signing key"
  type        = string
  sensitive   = true
}

variable "replica_count" {
  description = "Initial number of Observatory replicas"
  type        = number
  default     = 2
}

variable "enable_hpa" {
  description = "Enable Horizontal Pod Autoscaler"
  type        = bool
  default     = true
}

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
  default     = "0.2.0"
}
