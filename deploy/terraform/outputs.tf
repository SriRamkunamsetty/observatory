output "namespace" {
  description = "Deployed Kubernetes namespace"
  value       = kubernetes_namespace.observatory.metadata[0].name
}

output "helm_release_status" {
  description = "Status of the Helm release"
  value       = helm_release.observatory.status
}

output "service_endpoint" {
  description = "Internal Kubernetes Service DNS name"
  value       = "observatory.${kubernetes_namespace.observatory.metadata[0].name}.svc.cluster.local:8082"
}
