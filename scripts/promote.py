#!/usr/bin/env python3
"""Promotion d'un modele dans le registre MLflow par alias (staging puis production).

Variable requise : MLFLOW_TRACKING_URI
Codes de sortie : 0 = ok, 1 = erreur, 3 = promotion refusee par la regle de validation
"""
import argparse
import json
import sys

from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

MODEL = "task-priority-classifier"


def alias_version(client, alias):
    try:
        return client.get_model_version_by_alias(MODEL, alias)
    except MlflowException:
        return None


def metric(client, version, name):
    metrics = client.get_run(version.run_id).data.metrics
    if name not in metrics:
        sys.exit(f"metrique '{name}' absente du run, disponibles : {sorted(metrics)}")
    return metrics[name]


def to_staging(client, a):
    versions = client.search_model_versions(f"name='{MODEL}'")
    if not versions:
        sys.exit("aucune version dans le registre")
    cand = max(versions, key=lambda v: int(v.version)) if a.version is None \
        else next(v for v in versions if v.version == str(a.version))
    prod = alias_version(client, "production")

    score = metric(client, cand, a.metric)
    prod_score = metric(client, prod, a.metric) if prod else None
    ok = score >= a.min_score and (prod_score is None or score >= prod_score - a.tolerance)

    report = {
        "candidate_version": cand.version, "candidate_score": score,
        "production_version": prod.version if prod else None, "production_score": prod_score,
        "metric": a.metric, "min_score": a.min_score, "tolerance": a.tolerance,
        "decision": "promu en staging" if ok else "refuse",
    }
    print(json.dumps(report, indent=2))
    if not ok:
        return 3
    client.set_registered_model_alias(MODEL, "staging", cand.version)
    return 0


def to_production(client, _a):
    staging = alias_version(client, "staging")
    if staging is None:
        print("aucune version en staging", file=sys.stderr)
        return 1
    client.set_registered_model_alias(MODEL, "production", staging.version)
    print(json.dumps({"production_version": staging.version, "decision": "promu en production"}, indent=2))
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--to", choices=["staging", "production"], required=True)
    p.add_argument("--version", type=int, help="version precise (sinon la plus recente)")
    p.add_argument("--metric", default="f1_macro")
    p.add_argument("--min-score", type=float, default=0.70)
    p.add_argument("--tolerance", type=float, default=0.02)
    a = p.parse_args()
    client = MlflowClient()
    return to_staging(client, a) if a.to == "staging" else to_production(client, a)


if __name__ == "__main__":
    sys.exit(main())
