# ---------- Instance d'entrainement (spot) ----------

# Derniere image Amazon Linux 2023 publiee par AWS
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# Aucun acces entrant, sorties libres (installer des paquets, joindre MLflow)
resource "aws_security_group" "training" {
  name        = "${var.project}-training"
  description = "Training instance, no inbound access"
  vpc_id      = module.vpc.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_iam_policy_document" "training_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "training" {
  name               = "${var.project}-training"
  assume_role_policy = data.aws_iam_policy_document.training_trust.json
}

# Permet a GitHub Actions de piloter l'instance via SSM (pas de SSH)
resource "aws_iam_role_policy_attachment" "training_ssm" {
  role       = aws_iam_role.training.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "training_s3" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.mlflow.arn, "${aws_s3_bucket.mlflow.arn}/*"]
  }
}

resource "aws_iam_role_policy" "training_s3" {
  name   = "model-bucket"
  role   = aws_iam_role.training.id
  policy = data.aws_iam_policy_document.training_s3.json
}

resource "aws_iam_instance_profile" "training" {
  name = "${var.project}-training"
  role = aws_iam_role.training.name
}

# Modele de lancement : instance spot, supprimee a l'arret
resource "aws_launch_template" "training" {
  name          = "${var.project}-training"
  image_id      = data.aws_ssm_parameter.al2023.value
  instance_type = var.training_instance_type

  iam_instance_profile {
    name = aws_iam_instance_profile.training.name
  }

  vpc_security_group_ids               = [aws_security_group.training.id]
  instance_initiated_shutdown_behavior = "terminate"

  instance_market_options {
    market_type = "spot"
    spot_options {
      spot_instance_type             = "one-time"
      instance_interruption_behavior = "terminate"
    }
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2 obligatoire
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 20
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name    = "${var.project}-training"
      Project = var.project
      Role    = "training"
    }
  }
}

# Droits supplementaires pour GitHub Actions : lancer et suivre l'entrainement
data "aws_iam_policy_document" "github_training" {
  statement {
    sid       = "LaunchTraining"
    actions   = ["ec2:RunInstances"]
    resources = ["*"]
  }
  statement {
    sid       = "TagOnLaunch"
    actions   = ["ec2:CreateTags"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["RunInstances"]
    }
  }
  statement {
    sid       = "StopOnlyOurInstances"
    actions   = ["ec2:TerminateInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = [var.project]
    }
  }
  statement {
    sid       = "Describe"
    actions   = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus", "ec2:DescribeSubnets", "ssm:DescribeInstanceInformation", "ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
    resources = ["*"]
  }
  statement {
    sid       = "RunCommands"
    actions   = ["ssm:SendCommand"]
    resources = ["*"]
  }
  statement {
    sid       = "PassTrainingRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.training.arn]
  }
}

resource "aws_iam_role_policy" "github_training" {
  name   = "training"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_training.json
}

# ---------- Acces de MLflow a S3 (sans cle, via le compte de service) ----------

data "aws_iam_policy_document" "mlflow_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:sub"
      values   = ["system:serviceaccount:mlflow:mlflow"]
    }
    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "mlflow" {
  name               = "${var.project}-mlflow"
  assume_role_policy = data.aws_iam_policy_document.mlflow_trust.json
}

resource "aws_iam_role_policy" "mlflow_s3" {
  name   = "model-bucket"
  role   = aws_iam_role.mlflow.id
  policy = data.aws_iam_policy_document.training_s3.json
}

# MLflow est expose en NodePort : seule l'instance d'entrainement peut le joindre
resource "aws_vpc_security_group_ingress_rule" "mlflow_from_training" {
  security_group_id            = module.eks.node_security_group_id
  referenced_security_group_id = aws_security_group.training.id
  from_port                    = 30500
  to_port                      = 30500
  ip_protocol                  = "tcp"
  description                  = "MLflow NodePort depuis instance entrainement"
}
