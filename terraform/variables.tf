variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "dnt-mlflowops"
}

variable "k8s_version" {
  type    = string
  default = "1.36"
}

variable "github_repo" {
  type    = string
  default = "dnTmax/taskly"
}

variable "training_instance_type" {
  type    = string
  default = "t3.large"
}

# Nouveau format de l'identite GitHub (OIDC) : proprietaire@id/depot@id
variable "github_repo_sub" {
  type    = string
  default = "dnTmax@111535912/taskly@1451548208"
}
