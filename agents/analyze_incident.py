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
    "You analyze an incident on a prediction API. You receive a summary computed from the JSON "
    "logs: requests and errors per model version, error messages. Propose the probable cause "
    "and actions for the on-call person (roll back to a previous version, remove fault "
    "injection, check the logs). Answer with JSON "
    '{"verdict": "ok" | "warning" | "critical", "reasons": ["..."], "suggested_actions": ["..."]}.'
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
            v = str(d.get("model_version", "unknown"))
            if d.get("message") == "requete http":
                total += 1
                entry = versions.setdefault(v, {"requests": 0, "errors": 0})
                entry["requests"] += 1
                if int(d.get("status", 200)) >= 500:
                    errors += 1
                    entry["errors"] += 1
            if d.get("level") == "ERROR":
                text = (str(d.get("message", "")) + "\n" + str(d.get("exception", ""))).strip()
                if "simul" in line.lower():
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
        return {"verdict": "ok", "reasons": ["No errors in the provided logs."], "suggested_actions": []}
    rate = s["error_rate"]
    if s["simulated_faults"]:
        return {
            "verdict": "warning",
            "reasons": [f"{rate:.0%} errors, caused by fault injection (SIMULATE_ERROR_RATE)."],
            "suggested_actions": ["kubectl set env deployment/model-serving-api -n production SIMULATE_ERROR_RATE-"],
        }
    bad = [v for v, d in s["versions"].items() if d["errors"] > 0]
    good = [int(v) for v, d in s["versions"].items() if d["errors"] == 0 and d["requests"] > 0 and v.isdigit()]
    if len(bad) == 1 and good:
        return {
            "verdict": "critical" if rate > 0.05 else "warning",
            "reasons": [f"All errors ({rate:.0%}) come from version {bad[0]}; "
                        f"version {max(good)} has none."],
            "suggested_actions": [
                f"Point the production alias back to version {max(good)} (see README, rollback section)",
                "kubectl rollout restart deployment/model-serving-api -n production",
            ],
        }
    return {
        "verdict": "critical" if rate > 0.05 else "warning",
        "reasons": [f"{rate:.0%} errors with no obvious cause; messages: {s['error_messages']}."],
        "suggested_actions": ["kubectl logs -n production -l app=model-serving-api --tail=100",
                              "Check for fault injection: kubectl get deployment model-serving-api -n production -o jsonpath='{.spec.template.spec.containers[0].env}'"],
    }


def main():
    try:
        summary = summarize(sys.argv[1])
    except Exception as e:  # noqa: BLE001
        emit({"agent": "incident", "mode": "none", "verdict": "unavailable",
              "reasons": [f"unreadable logs : {type(e).__name__}"]})
        return
    result = run_agent("incident", SYSTEM, summary, simulate, ["verdict", "reasons"])
    result["summary"] = summary
    emit(result)


if __name__ == "__main__":
    main()

