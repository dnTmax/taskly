data "aws_availability_zones" "available" {
  state = "available"
}

# Reseau dedie au projet
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name = "${var.project}-vpc"
  cidr = "10.20.0.0/16"

  azs             = slice(data.aws_availability_zones.available.names, 0, 2)
  private_subnets = ["10.20.1.0/24", "10.20.2.0/24"]
  public_subnets  = ["10.20.101.0/24", "10.20.102.0/24"]

  enable_nat_gateway = true
  single_nat_gateway = true # une seule NAT pour reduire le cout

  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
}

# Cluster Kubernetes
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.31"

  cluster_name    = var.project
  cluster_version = var.k8s_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access           = true
  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns        = {}
    kube-proxy     = {}
    vpc-cni        = {}
    metrics-server = {} # necessaire pour l'autoscaling (HPA)
  }

  eks_managed_node_groups = {
    main = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = ["t3.large"]
      min_size       = 2
      max_size       = 3
      desired_size   = 2
    }
  }
}

# Le plan de controle doit joindre le metrics-server gere par EKS (port 10251)
resource "aws_vpc_security_group_ingress_rule" "metrics_server" {
  security_group_id            = module.eks.node_security_group_id
  referenced_security_group_id = module.eks.cluster_security_group_id
  from_port                    = 10251
  to_port                      = 10251
  ip_protocol                  = "tcp"
  description                  = "Plan de controle vers metrics-server (port 10251)"
}
