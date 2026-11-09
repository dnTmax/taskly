output "mlflow_bucket" {
  value = aws_s3_bucket.mlflow.bucket
}

output "cluster_name" {
  value = module.eks.cluster_name
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}

output "mlflow_role_arn" {
  value = aws_iam_role.mlflow.arn
}

output "training_launch_template" {
  value = aws_launch_template.training.name
}

output "training_subnet_id" {
  value = module.vpc.private_subnets[0]
}
