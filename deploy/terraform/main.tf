terraform {
  required_version = ">= 1.5.0"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.26.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.12.0"
    }
  }
}

resource "kubernetes_namespace" "observatory" {
  metadata {
    name = var.namespace
    labels = {
      environment                        = var.environment
      "app.kubernetes.io/managed-by"     = "terraform"
      "pod-security.kubernetes.io/enforce" = "restricted"
    }
  }
}

resource "kubernetes_secret" "observatory_secrets" {
  metadata {
    name      = "observatory-secrets"
    namespace = kubernetes_namespace.observatory.metadata[0].name
  }

  data = {
    API_KEY    = var.api_key
    SECRET_KEY = var.secret_key
  }

  type = "Opaque"
}

resource "helm_release" "observatory" {
  name       = "observatory"
  namespace  = kubernetes_namespace.observatory.metadata[0].name
  chart      = "${path.module}/../helm/observatory"
  version    = "0.2.0"

  values = [
    yamlencode({
      replicaCount = var.replica_count
      image = {
        tag = var.image_tag
      }
      existingSecret = kubernetes_secret.observatory_secrets.metadata[0].name
      autoscaling = {
        enabled = var.enable_hpa
      }
    })
  ]

  depends_on = [
    kubernetes_secret.observatory_secrets
  ]
}
