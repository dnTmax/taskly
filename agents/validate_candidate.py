#!/usr/bin/env python3
"""Agent 1, validation : relit le rapport de promotion et donne un avis consultatif.

La regle deterministe de scripts/promote.py reste la seule barriere : cet agent ne promeut
et ne refuse rien, il aide a comprendre.

Usage : validate_candidate.py rapport.json   (sortie de promote.py --to staging)
"""
import json
import sys

from common import emit, run_agent

SYSTEM = (
    "You validate a candidate model before promotion. You receive the promotion report "
    "(candidate and production scores, thresholds, decision). Look for what the automatic rule "
    "cannot see: suspicious score (perfect, data leakage), a drop even within tolerance, "
    "doubtful training data. Answer with JSON "
    '{"verdict": "ok" | "warning" | "block", "reasons": ["..."]}.'
)


def simulate(p):
    cand, prod = p.get("candidate_score"), p.get("production_score")
    if p.get("decision") == "refuse":
        return {"verdict": "block", "reasons": ["The deterministic rule rejected this version."]}
    verdict, reasons = "ok", []
    if cand is not None and cand >= 0.999:
        verdict = "warning"
        reasons.append("Perfect score: check for data leakage or a test set that is too easy "
                       "(synthetic data?).")
    if cand is not None and prod is not None and cand < prod:
        verdict = "warning"
        reasons.append(f"Score lower than production ({cand:.3f} vs {prod:.3f}), "
                       "even though it stays within tolerance.")
    if not reasons:
        reasons.append("Score at least equal to production, no anomaly detected.")
    return {"verdict": verdict, "reasons": reasons}


def main():
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            report = json.load(f)
    except Exception as e:  # noqa: BLE001 - un agent ne doit jamais casser le pipeline
        emit({"agent": "validation", "mode": "none", "verdict": "unavailable",
              "reasons": [f"unreadable report : {type(e).__name__}"]})
        return
    emit(run_agent("validation", SYSTEM, report, simulate, ["verdict", "reasons"]))


if __name__ == "__main__":
    main()

