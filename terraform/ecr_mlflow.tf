# Depot de l'image du serveur MLflow
resource "aws_ecr_repository" "mlflow" {
  name         = "${var.project}-mlflow"
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

output "ecr_mlflow_url" {
  value = aws_ecr_repository.mlflow.repository_url
}
