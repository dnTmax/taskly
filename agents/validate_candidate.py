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
    "Tu valides un modele candidat avant sa promotion. Tu recois le rapport de promotion "
    "(scores du candidat et de la production, seuils, decision). Cherche ce que la regle "
    "automatique ne voit pas : score suspect (parfait, fuite de donnees), baisse meme dans "
    "la tolerance, donnees d'entrainement douteuses. Reponds par un JSON "
    '{"verdict": "ok" | "attention" | "bloquer", "reasons": ["..."]}.'
)


def simulate(p):
    cand, prod = p.get("candidate_score"), p.get("production_score")
    if p.get("decision") == "refuse":
        return {"verdict": "bloquer", "reasons": ["La regle deterministe a refuse cette version."]}
    verdict, reasons = "ok", []
    if cand is not None and cand >= 0.999:
        verdict = "attention"
        reasons.append("Score parfait : verifier une fuite de donnees ou un jeu de test trop facile "
                       "(donnees synthetiques ?).")
    if cand is not None and prod is not None and cand < prod:
        verdict = "attention"
        reasons.append(f"Score inferieur a la production ({cand:.3f} contre {prod:.3f}), "
                       "meme s'il reste dans la tolerance.")
    if not reasons:
        reasons.append("Score au moins egal a celui de la production, aucune anomalie detectee.")
    return {"verdict": verdict, "reasons": reasons}


def main():
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            report = json.load(f)
    except Exception as e:  # noqa: BLE001 - un agent ne doit jamais casser le pipeline
        emit({"agent": "validation", "mode": "aucun", "verdict": "indisponible",
              "reasons": [f"rapport illisible : {type(e).__name__}"]})
        return
    emit(run_agent("validation", SYSTEM, report, simulate, ["verdict", "reasons"]))


if __name__ == "__main__":
    main()
