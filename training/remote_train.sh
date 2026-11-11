#!/bin/bash
# Execute sur l'instance spot (root, via SSM), depuis /opt/train ou les sources ont ete extraites.
# Variable requise : MLFLOW_TRACKING_URI
set -euo pipefail
export GIT_PYTHON_REFRESH=quiet
: "${MLFLOW_TRACKING_URI:?variable manquante}"

echo "== Installation de Docker et Python 3.12"
dnf install -y -q docker python3.12
systemctl start docker

echo "== Base Postgres locale et temporaire (mot de passe aleatoire, aucun acces entrant)"
DB_PASSWORD=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
docker run -d --name db -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=tasks -e POSTGRES_PASSWORD="$DB_PASSWORD" -e POSTGRES_DB=tasks \
  public.ecr.aws/docker/library/postgres:16 > /dev/null
# Postgres redemarre une fois apres son initialisation : on attend les deux messages "pret"
until [ "$(docker logs db 2>&1 | grep -c 'ready to accept connections')" -ge 2 ]; do sleep 1; done

echo "== Environnement Python"
python3.12 -m venv venv
venv/bin/pip install -q -r training/requirements.txt

export DATABASE_URL="postgresql+psycopg2://tasks:${DB_PASSWORD}@127.0.0.1:5432/tasks"
echo "== Generation des donnees"
venv/bin/python model/generate_data.py | sed "s/${DB_PASSWORD}/***/g"
echo "== Entrainement (MLflow : $MLFLOW_TRACKING_URI)"
venv/bin/python model/train.py
echo "== Entrainement termine"
