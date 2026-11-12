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
    "You help a person decide whether to approve a model for production. "
    "You receive measurements taken on the staging environment: expected and served version, "
    "number of requests, errors, p95 latency in milliseconds. Answer with JSON "
    '{"verdict": "ok" | "warning" | "block", "reasons": ["..."]}.'
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
    served = "unknown"
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
        return {"verdict": "block", "reasons": [
            f"Staging serves version {s['served_version']} instead of {s['expected_version']}."]}
    if s["error_rate"] > 0.05:
        return {"verdict": "block", "reasons": [f"Error rate of {s['error_rate']:.1%} on staging."]}
    reasons = []
    if s["errors"] > 0:
        reasons.append(f"{s['errors']} error(s) out of {s['requests']} requests.")
    if s["p95_ms"] > 500:
        reasons.append(f"High p95 latency: {s['p95_ms']} ms.")
    if reasons:
        return {"verdict": "warning", "reasons": reasons}
    return {"verdict": "ok", "reasons": [
        f"Version {s['served_version']} served, 0 errors out of {s['requests']} requests, p95 {s['p95_ms']} ms."]}


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

