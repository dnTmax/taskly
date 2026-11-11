#!/usr/bin/env python3
"""Agent 3, analyse d'incident : lit les journaux JSON de l'API et propose une cause probable.

Il ne corrige rien : il propose des actions que la personne d'astreinte decide d'executer.

Usage : analyze_incident.py journaux.jsonl
Recuperer les journaux : kubectl logs -n production -l app=model-serving-api --tail=-1 | grep '^{' > journaux.jsonl
"""
import json
import sys

from common import emit, run_agent

SYSTEM = (
    "Tu analyses un incident sur une API de prediction. Tu recois un resume calcule a partir des "
    "journaux JSON : requetes et erreurs par version du modele, messages d'erreur. Propose la cause "
    "probable et des actions pour la personne d'astreinte (retour a une version precedente, "
    "retrait d'une injection de pannes, verification des journaux). Reponds par un JSON "
    '{"verdict": "ok" | "attention" | "critique", "reasons": ["..."], "suggested_actions": ["..."]}.'
)


def summarize(path):
    versions, messages = {}, set()
    total = errors = 0
    simulated = False
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            v = str(d.get("model_version", "inconnue"))
            if d.get("message") == "requete http":
                total += 1
                entry = versions.setdefault(v, {"requests": 0, "errors": 0})
                entry["requests"] += 1
                if int(d.get("status", 200)) >= 500:
                    errors += 1
                    entry["errors"] += 1
            if d.get("level") == "ERROR":
                text = (str(d.get("message", "")) + "\n" + str(d.get("exception", ""))).strip()
                if "erreur simulee" in text:
                    simulated = True
                messages.add(text.splitlines()[-1][:120])
    return {
        "requests": total, "errors": errors,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "versions": versions, "error_messages": sorted(messages)[:3],
        "simulated_faults": simulated,
    }


def simulate(s):
    if s["errors"] == 0:
        return {"verdict": "ok", "reasons": ["Aucune erreur dans les journaux fournis."], "suggested_actions": []}
    rate = s["error_rate"]
    if s["simulated_faults"]:
        return {
            "verdict": "attention",
            "reasons": [f"{rate:.0%} d'erreurs, causees par l'injection de pannes (SIMULATE_ERROR_RATE)."],
            "suggested_actions": ["kubectl set env deployment/model-serving-api -n production SIMULATE_ERROR_RATE-"],
        }
    bad = [v for v, d in s["versions"].items() if d["errors"] > 0]
    good = [int(v) for v, d in s["versions"].items() if d["errors"] == 0 and d["requests"] > 0 and v.isdigit()]
    if len(bad) == 1 and good:
        return {
            "verdict": "critique" if rate > 0.05 else "attention",
            "reasons": [f"Toutes les erreurs ({rate:.0%}) viennent de la version {bad[0]} ; "
                        f"la version {max(good)} n'en a aucune."],
            "suggested_actions": [
                f"Remettre l'alias production sur la version {max(good)} (voir README, section revenir en arriere)",
                "kubectl rollout restart deployment/model-serving-api -n production",
            ],
        }
    return {
        "verdict": "critique" if rate > 0.05 else "attention",
        "reasons": [f"{rate:.0%} d'erreurs sans cause evidente ; messages : {s['error_messages']}."],
        "suggested_actions": ["kubectl logs -n production -l app=model-serving-api --tail=100"],
    }


def main():
    try:
        summary = summarize(sys.argv[1])
    except Exception as e:  # noqa: BLE001
        emit({"agent": "incident", "mode": "aucun", "verdict": "indisponible",
              "reasons": [f"journaux illisibles : {type(e).__name__}"]})
        return
    result = run_agent("incident", SYSTEM, summary, simulate, ["verdict", "reasons"])
    result["summary"] = summary
    emit(result)


if __name__ == "__main__":
    main()
