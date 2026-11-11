# MLFlowOps: train, version and serve a task-priority classifier on AWS

English | [Français](README.fr.md)

This project trains, versions and serves a machine-learning model that predicts the
priority (`low`, `medium`, `high`) of a task from its title and description. Everything
around the model follows DevOps practices: infrastructure as code, a model registry,
spot-instance training, a CI/CD pipeline with a manual approval, monitoring and alerts.

The original Tasks API that this repository started from is documented in
[docs/TASKS-API.md](docs/TASKS-API.md). Design choices and their reasons are in
[docs/DECISIONS.md](docs/DECISIONS.md).

## Architecture

```mermaid
flowchart TB
  dev[Developer] -->|Run workflow| train
  subgraph gha[GitHub Actions - AWS access through OIDC, no stored keys]
    train[1 Train on a spot instance] --> promote[2 Promote to staging]
    train --> build[3 Build the API image]
    promote --> deps[4 Deploy staging]
    build --> deps
    deps --> approve{5 Manual approval}
    approve --> depp[6 Promote and deploy production]
  end
  train -->|SSM Run Command| spot[EC2 spot instance]
  spot -->|log and register the model| mlflow
  build --> ecr[(ECR)]
  mlflow --> s3[(S3 artifacts)]
  subgraph eks[EKS cluster dnt-mlflowops]
    mlflow[MLflow server] --> pg[(Postgres)]
    stg[API staging alias staging]
    prod[API production alias production]
    stg -->|load the model at startup| mlflow
    prod -->|load the model at startup| mlflow
    mon[Prometheus and Grafana] -->|scrape metrics| stg
    mon -->|scrape metrics| prod
  end
  promote -->|set alias staging| mlflow
  depp -->|set alias production| mlflow
```

| Component | Role |
|---|---|
| Terraform | VPC, EKS, S3 bucket, ECR repositories, IAM roles (IRSA, GitHub OIDC), spot launch template |
| MLflow + Postgres (EKS) | Experiment tracking and Model Registry; artifacts stored in S3 through the server (proxy mode) |
| Spot instance | Short-lived machine that trains the model, driven through SSM (no SSH) |
| GitHub Actions | Orchestrates train, promote, build, deploy, approval |
| API (FastAPI, EKS) | `POST /predict`; loads the model from the registry at startup by alias |
| Prometheus + Grafana | Metrics, dashboard with an environment selector, alert above 5 % errors |

## Repository layout

| Path | Content |
|---|---|
| `terraform/` | All AWS infrastructure |
| `training/` | Pinned requirements and the script that runs on the spot instance |
| `scripts/run_training.py` | Launches the spot instance, trains through SSM, always terminates the instance |
| `scripts/promote.py` | Promotes a model version with aliases, with a validation rule |
| `model/` | Data generation and training code |
| `serving/` | The prediction API and its Dockerfile |
| `mlflow-server/` | Dockerfile of the MLflow server image |
| `k8s/` | Kubernetes manifests (MLflow, Postgres, monitoring) |
| `k8s/serving/` | Kustomize base and overlays for `staging` and `production` |
| `.github/workflows/mlops.yml` | The CI/CD pipeline |

## Prerequisites

AWS account and credentials, Terraform, kubectl, Helm, Docker, the AWS CLI, Python 3.12,
and a GitHub repository with two environments named `staging` and `production`
(Settings, Environments). On `production`, enable **Required reviewers**.

## Deploy from scratch

1. Infrastructure (about 15 minutes):

```bash
cd terraform
terraform init
terraform apply
$(terraform output -raw kubeconfig_command)
cd ..
```

2. Build and push the MLflow image (add `--platform linux/amd64` when building on an ARM machine):

```bash
ECR_MLFLOW=$(terraform -chdir=terraform output -raw ecr_mlflow_url)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "${ECR_MLFLOW%%/*}"
docker build --platform linux/amd64 -t "$ECR_MLFLOW:2.22.5-r2" mlflow-server
docker push "$ECR_MLFLOW:2.22.5-r2"
```

3. Namespace, storage, secrets, Postgres and MLflow. Secrets are generated at deploy time and
never stored in Git:

```bash
kubectl apply -f k8s/namespace.yaml -f k8s/storageclass.yaml
PGPASS=$(openssl rand -hex 16)
kubectl create secret generic postgres -n mlflow --from-literal=password="$PGPASS"
kubectl create secret generic mlflow-backend -n mlflow \
  --from-literal=uri="postgresql+psycopg2://mlflow:${PGPASS}@postgres.mlflow.svc.cluster.local:5432/mlflow"
unset PGPASS
kubectl apply -f k8s/postgres.yaml
kubectl rollout status statefulset/postgres -n mlflow
kubectl apply -f k8s/mlflow.yaml -f k8s/mlflow-nodeport.yaml
kubectl rollout status deployment/mlflow -n mlflow
```

4. Monitoring and dashboard:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install monitoring prometheus-community/kube-prometheus-stack --version 93.1.1 \
  -n monitoring --create-namespace -f k8s/monitoring-values.yaml
kubectl create configmap dnt-prediction-dashboard -n monitoring \
  --from-file=prediction.json=k8s/dashboards/prediction.json
kubectl label configmap dnt-prediction-dashboard -n monitoring grafana_dashboard=1
```

5. Connect GitHub to AWS: set `ROLE_ARN` in `.github/workflows/mlops.yml` to the Terraform output
`github_actions_role_arn`, and set `github_repo` and `github_repo_sub` in the Terraform variables
to match your repository (the OIDC subject contains the owner and repository numeric IDs).

6. Run the pipeline for the first time (see the next section). It creates the `staging` and
`production` namespaces, trains the first model and puts it in production.

## Retrain a model

On GitHub: **Actions**, **MLOps pipeline**, **Run workflow**. Or with the GitHub CLI:
`gh workflow run mlops.yml`.

The pipeline then runs, with no manual command:

1. **Train**: a spot instance is launched (other zones and instance types are tried if there is no capacity, and training is retried once if the spot is reclaimed), the model is trained and registered in MLflow, and the instance is always terminated.
2. **Promote to staging**: the newest version gets the `staging` alias only if it passes the validation rule (F1 macro at least 0.70, and not more than 0.02 below the production version).
3. **Build**: the API image is built and pushed to ECR, tagged with the commit.
4. **Deploy staging** and check that staging serves the promoted version.
5. **Manual approval** of the `production` environment.
6. **Promote and deploy production**: the `production` alias moves to the staging version, and the API is updated with a rolling update (no downtime), then checked.

## Promote a model by hand, and roll back

Model versions are switched with MLflow aliases: no Kubernetes manifest changes.

```bash
kubectl port-forward -n mlflow svc/mlflow 5001:5000 &
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001
pip install mlflow-skinny==2.22.5

python scripts/promote.py --to staging               # newest version, with the validation rule
python scripts/promote.py --to staging --version 5   # a chosen version
python scripts/promote.py --to production            # the staging version goes to production
kubectl rollout restart deployment/model-serving-api -n production
```

Roll back production to an earlier version (here version 3):

```bash
python - <<'PY'
from mlflow import MlflowClient
MlflowClient().set_registered_model_alias("task-priority-classifier", "production", "3")
PY
kubectl rollout restart deployment/model-serving-api -n production
```

## Observability

- **Metrics** (`/metrics`): `predict_requests_total` (by status and model version), `predict_latency_seconds` (histogram), `model_info` (served model version).
- **Dashboard**: request rate, error rate, latency p95 and model version, with an environment selector (`staging` or `production`).
- **Alert**: `PredictionErrorRateHigh` fires when more than 5 % of predictions fail for 2 minutes, in production.
- **Logs**: one JSON line per request with `model_version`, method, path, status and duration. Health probes are not logged.

Test the alert by making 30 % of predictions fail, then remove the variable:

```bash
kubectl set env deployment/model-serving-api -n production SIMULATE_ERROR_RATE=0.3
# ... send traffic for about 8 minutes, watch the alert ...
kubectl set env deployment/model-serving-api -n production SIMULATE_ERROR_RATE-
```

Grafana admin password: `kubectl get secret monitoring-grafana -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d`

## Cost control

The training machine exists only during training (about 4 minutes) and is a spot instance.
There is no load balancer and a single NAT gateway. When you stop working, destroy everything
and rebuild it later with the steps above:

```bash
cd terraform && terraform destroy
```

## Known issues and how they were solved

See [docs/DECISIONS.md](docs/DECISIONS.md), section "Problems met and how they were solved".

---

Dally NTOLO, DevOps & Cloud
