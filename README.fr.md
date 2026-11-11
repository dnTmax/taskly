# MLFlowOps : entraîner, versionner et servir un classifieur de priorité de tâches sur AWS

[English](README.md) | Français

Ce projet entraîne, versionne et sert un modèle d'apprentissage automatique qui prédit la
priorité (`low`, `medium`, `high`) d'une tâche à partir de son titre et de sa description. Tout
ce qui entoure le modèle suit les pratiques DevOps : infrastructure as code, registre de modèles,
entraînement sur instance spot, pipeline CI/CD avec approbation manuelle, supervision et alertes.

L'application Tasks API d'origine, à partir de laquelle ce dépôt a été créé, est décrite dans
[docs/TASKS-API.md](docs/TASKS-API.md). Les choix de conception et leurs raisons sont dans
[docs/DECISIONS.fr.md](docs/DECISIONS.fr.md).

## Architecture

```mermaid
flowchart TB
  dev[Développeur] -->|Run workflow| train
  subgraph gha[GitHub Actions - accès AWS par OIDC, aucune clé stockée]
    train[1 Entraîner sur instance spot] --> promote[2 Promouvoir en staging]
    train --> build[3 Construire l'image de l'API]
    promote --> deps[4 Déployer staging]
    build --> deps
    deps --> approve{5 Approbation manuelle}
    approve --> depp[6 Promouvoir et déployer production]
  end
  train -->|SSM Run Command| spot[Instance EC2 spot]
  spot -->|journalise et enregistre le modèle| mlflow
  build --> ecr[(ECR)]
  mlflow --> s3[(Artefacts S3)]
  subgraph eks[Cluster EKS dnt-mlflowops]
    mlflow[Serveur MLflow] --> pg[(Postgres)]
    stg[API staging alias staging]
    prod[API production alias production]
    stg -->|charge le modèle au démarrage| mlflow
    prod -->|charge le modèle au démarrage| mlflow
    mon[Prometheus et Grafana] -->|collecte les métriques| stg
    mon -->|collecte les métriques| prod
  end
  promote -->|pose l'alias staging| mlflow
  depp -->|pose l'alias production| mlflow
```

| Composant | Rôle |
|---|---|
| Terraform | VPC, EKS, bucket S3, dépôts ECR, rôles IAM (IRSA, OIDC GitHub), modèle de lancement spot |
| MLflow + Postgres (EKS) | Suivi des expériences et registre de modèles ; artefacts stockés dans S3 via le serveur (mode proxy) |
| Instance spot | Machine éphémère qui entraîne le modèle, pilotée par SSM (pas de SSH) |
| GitHub Actions | Orchestre entraînement, promotion, construction, déploiement, approbation |
| API (FastAPI, EKS) | `POST /predict` ; charge le modèle depuis le registre au démarrage, par alias |
| Prometheus + Grafana | Métriques, tableau de bord avec sélecteur d'environnement, alerte au-delà de 5 % d'erreurs |

## Organisation du dépôt

| Chemin | Contenu |
|---|---|
| `terraform/` | Toute l'infrastructure AWS |
| `training/` | Dépendances épinglées et script exécuté sur l'instance spot |
| `scripts/run_training.py` | Lance l'instance spot, entraîne via SSM, supprime toujours l'instance |
| `scripts/promote.py` | Promeut une version de modèle par alias, avec une règle de validation |
| `model/` | Génération des données et code d'entraînement |
| `serving/` | L'API de prédiction et son Dockerfile |
| `mlflow-server/` | Dockerfile de l'image du serveur MLflow |
| `k8s/` | Manifestes Kubernetes (MLflow, Postgres, supervision) |
| `k8s/serving/` | Base et surcharges Kustomize pour `staging` et `production` |
| `.github/workflows/mlops.yml` | Le pipeline CI/CD |

## Prérequis

Un compte et des identifiants AWS, Terraform, kubectl, Helm, Docker, l'AWS CLI, Python 3.12,
et un dépôt GitHub avec deux environnements nommés `staging` et `production`
(Settings, Environments). Sur `production`, activer **Required reviewers**.

## Déployer depuis zéro

1. Infrastructure (environ 15 minutes) :

```bash
cd terraform
terraform init
terraform apply
$(terraform output -raw kubeconfig_command)
cd ..
```

2. Construire et pousser l'image MLflow (ajouter `--platform linux/amd64` sur une machine ARM) :

```bash
ECR_MLFLOW=$(terraform -chdir=terraform output -raw ecr_mlflow_url)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "${ECR_MLFLOW%%/*}"
docker build --platform linux/amd64 -t "$ECR_MLFLOW:2.22.5-r2" mlflow-server
docker push "$ECR_MLFLOW:2.22.5-r2"
```

3. Namespace, stockage, secrets, Postgres et MLflow. Les secrets sont générés au moment du
déploiement et ne sont jamais stockés dans Git :

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

4. Supervision et tableau de bord :

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install monitoring prometheus-community/kube-prometheus-stack --version 93.1.1 \
  -n monitoring --create-namespace -f k8s/monitoring-values.yaml
kubectl create configmap dnt-prediction-dashboard -n monitoring \
  --from-file=prediction.json=k8s/dashboards/prediction.json
kubectl label configmap dnt-prediction-dashboard -n monitoring grafana_dashboard=1
```

5. Relier GitHub à AWS : renseigner `ROLE_ARN` dans `.github/workflows/mlops.yml` avec la sortie
Terraform `github_actions_role_arn`, et renseigner `github_repo` et `github_repo_sub` dans les
variables Terraform pour qu'ils correspondent au dépôt (l'identité OIDC contient les identifiants
numériques du propriétaire et du dépôt).

6. Lancer le pipeline une première fois (section suivante). Il crée les namespaces `staging` et
`production`, entraîne le premier modèle et le met en production.

## Réentraîner un modèle

Sur GitHub : **Actions**, **MLOps pipeline**, **Run workflow**. Ou avec la CLI GitHub :
`gh workflow run mlops.yml`.

Le pipeline enchaîne alors, sans aucune commande manuelle :

1. **Entraînement** : une instance spot est lancée (d'autres zones et types de machine sont essayés s'il n'y a pas de capacité, et l'entraînement est relancé une fois si le spot est repris), le modèle est entraîné et enregistré dans MLflow, et l'instance est toujours supprimée.
2. **Promotion en staging** : la version la plus récente reçoit l'alias `staging` seulement si elle passe la règle de validation (F1 macro d'au moins 0,70 et pas plus de 0,02 sous la version de production).
3. **Construction** : l'image de l'API est construite et poussée dans ECR, étiquetée avec le commit.
4. **Déploiement en staging**, puis vérification que staging sert la version promue.
5. **Approbation manuelle** de l'environnement `production`.
6. **Promotion et déploiement en production** : l'alias `production` passe sur la version de staging, et l'API est mise à jour progressivement (sans coupure), puis vérifiée.

## Promouvoir un modèle à la main, et revenir en arrière

Les versions du modèle se changent avec les alias MLflow : aucun manifeste Kubernetes à modifier.

```bash
kubectl port-forward -n mlflow svc/mlflow 5001:5000 &
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001
pip install mlflow-skinny==2.22.5

python scripts/promote.py --to staging               # version la plus récente, avec la règle de validation
python scripts/promote.py --to staging --version 5   # une version choisie
python scripts/promote.py --to production            # la version de staging passe en production
kubectl rollout restart deployment/model-serving-api -n production
```

Revenir à une version antérieure en production (ici la version 3) :

```bash
python - <<'PY'
from mlflow import MlflowClient
MlflowClient().set_registered_model_alias("task-priority-classifier", "production", "3")
PY
kubectl rollout restart deployment/model-serving-api -n production
```

## Supervision

- **Métriques** (`/metrics`) : `predict_requests_total` (par statut et version du modèle), `predict_latency_seconds` (histogramme), `model_info` (version du modèle servie).
- **Tableau de bord** : débit de requêtes, taux d'erreurs, latence p95 et version du modèle, avec un sélecteur d'environnement (`staging` ou `production`).
- **Alerte** : `PredictionErrorRateHigh` se déclenche quand plus de 5 % des prédictions échouent pendant 2 minutes, en production.
- **Journaux** : une ligne JSON par requête avec `model_version`, méthode, chemin, statut et durée. Les sondes de santé ne sont pas journalisées.

Tester l'alerte en faisant échouer 30 % des prédictions, puis retirer la variable :

```bash
kubectl set env deployment/model-serving-api -n production SIMULATE_ERROR_RATE=0.3
# ... envoyer du trafic pendant environ 8 minutes, observer l'alerte ...
kubectl set env deployment/model-serving-api -n production SIMULATE_ERROR_RATE-
```

Mot de passe administrateur de Grafana : `kubectl get secret monitoring-grafana -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d`

## Maîtrise des coûts

La machine d'entraînement n'existe que pendant l'entraînement (environ 4 minutes) et c'est une
instance spot. Il n'y a aucun load balancer et une seule NAT gateway. Quand le travail s'arrête,
tout peut être détruit puis reconstruit avec les étapes ci-dessus :

```bash
cd terraform && terraform destroy
```

## Problèmes rencontrés et résolus

Voir [docs/DECISIONS.fr.md](docs/DECISIONS.fr.md), section « Problèmes rencontrés et résolus ».

---

Dally NTOLO, DevOps & Cloud
