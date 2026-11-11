#!/usr/bin/env python3
"""Agent 2, controle de staging : donne un avis a la personne qui doit approuver la production.

Le code collecte des mesures (requetes de test, version servie) ; l'agent les interprete.
Il ne deploie rien et n'approuve rien : l'approbation reste humaine.

Usage : check_staging.py --url http://127.0.0.1:8101 --expected-version 7 [--requests 30]
"""
import argparse
import json
import re
import time
import urllib.request

from common import emit, run_agent

SYSTEM = (
    "Tu aides une personne a decider d'approuver ou non le passage d'un modele en production. "
    "Tu recois des mesures prises sur l'environnement de staging : version attendue et servie, "
    "nombre de requetes, erreurs, latence p95 en millisecondes. Reponds par un JSON "
    '{"verdict": "ok" | "attention" | "bloquer", "reasons": ["..."]}.'
)

TITLES = [
    "Corriger le bug de paiement", "Ranger la salle de reunion", "Preparer la presentation client",
    "Mettre a jour la documentation", "Repondre a l'incident de production",
]


def collect(url, expected, n):
    errors, latencies = 0, []
    for i in range(n):
        body = json.dumps({"title": TITLES[i % len(TITLES)]}).encode()
        req = urllib.request.Request(url + "/predict", data=body, headers={"Content-Type": "application/json"})
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception:  # noqa: BLE001
            errors += 1
        latencies.append((time.perf_counter() - start) * 1000)
    latencies.sort()
    served = "inconnue"
    try:
        with urllib.request.urlopen(url + "/metrics", timeout=5) as r:
            m = re.search(r'model_info\{[^}]*model_version="([^"]+)"', r.read().decode())
            if m:
                served = m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return {
        "expected_version": str(expected), "served_version": served,
        "requests": n, "errors": errors, "error_rate": round(errors / n, 4),
        "p95_ms": round(latencies[int(0.95 * (len(latencies) - 1))], 1),
    }


def simulate(s):
    if s["served_version"] != s["expected_version"]:
        return {"verdict": "bloquer", "reasons": [
            f"Staging sert la version {s['served_version']} au lieu de {s['expected_version']}."]}
    if s["error_rate"] > 0.05:
        return {"verdict": "bloquer", "reasons": [f"Taux d'erreur de {s['error_rate']:.1%} sur staging."]}
    reasons = []
    if s["errors"] > 0:
        reasons.append(f"{s['errors']} erreur(s) sur {s['requests']} requetes.")
    if s["p95_ms"] > 500:
        reasons.append(f"Latence p95 elevee : {s['p95_ms']} ms.")
    if reasons:
        return {"verdict": "attention", "reasons": reasons}
    return {"verdict": "ok", "reasons": [
        f"Version {s['served_version']} servie, 0 erreur sur {s['requests']} requetes, p95 {s['p95_ms']} ms."]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--expected-version", required=True)
    p.add_argument("--requests", type=int, default=30)
    a = p.parse_args()
    snapshot = collect(a.url, a.expected_version, a.requests)
    result = run_agent("staging", SYSTEM, snapshot, simulate, ["verdict", "reasons"])
    result["measures"] = snapshot
    emit(result)


if __name__ == "__main__":
    main()
