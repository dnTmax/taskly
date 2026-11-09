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
