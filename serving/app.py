"""API de service : charge le modele depuis le Model Registry MLflow."""
import json
import logging
import os
import random
import time
from contextlib import asynccontextmanager

import mlflow.sklearn
from fastapi import FastAPI, HTTPException, Response
from mlflow import MlflowClient
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel

MODEL_NAME = os.environ.get("MODEL_NAME", "task-priority-classifier")
MODEL_ALIAS = os.environ.get("MODEL_ALIAS", "production")
# Test d'alerte : part des predictions qui echouent volontairement (0 = aucune)
SIMULATE_ERROR_RATE = float(os.environ.get("SIMULATE_ERROR_RATE", "0"))

state = {"model": None, "version": "none"}


class JsonFormatter(logging.Formatter):
    """Chaque ligne de log est un JSON qui contient la version du modele."""

    def format(self, record):
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "model_name": MODEL_NAME,
            "model_version": state["version"],
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger = logging.getLogger("serving")
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.propagate = False

REQUESTS = Counter(
    "predict_requests_total", "Nombre de predictions", ["status", "model_version"]
)
LATENCY = Histogram(
    "predict_latency_seconds", "Duree d'une prediction en secondes", ["model_version"]
)
MODEL_INFO = Gauge(
    "model_info", "Modele actuellement servi", ["model_name", "model_version"]
)


def load_model() -> None:
    client = MlflowClient()  # utilise MLFLOW_TRACKING_URI
    mv = client.get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
    # On charge par numero de version (et non par alias) : pas de risque que
    # l'alias change entre les deux appels.
    model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}/{mv.version}")
    state["model"] = model
    state["version"] = str(mv.version)
    MODEL_INFO.labels(MODEL_NAME, state["version"]).set(1)
    # Series a 0 des le depart : le taux d'erreur est calculable avant la 1re erreur
    for status in ("success", "error"):
        REQUESTS.labels(status, state["version"])
    logger.info("modele charge depuis le registry (alias %s)", MODEL_ALIAS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model()
    yield


app = FastAPI(title="task-priority-serving", lifespan=lifespan)


class TaskIn(BaseModel):
    title: str
    description: str = ""


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    if state["model"] is None:
        raise HTTPException(status_code=503, detail="modele non charge")
    return {"status": "ready", "model_version": state["version"]}


@app.post("/predict")
def predict(task: TaskIn):
    version = state["version"]
    start = time.perf_counter()
    try:
        if random.random() < SIMULATE_ERROR_RATE:
            raise RuntimeError("erreur simulee (test d'alerte)")
        label = state["model"].predict([f"{task.title} {task.description}"])[0]
    except Exception:
        REQUESTS.labels("error", version).inc()
        logger.exception("erreur de prediction")
        raise HTTPException(status_code=500, detail="erreur de prediction")
    LATENCY.labels(version).observe(time.perf_counter() - start)
    REQUESTS.labels("success", version).inc()
    logger.info("prediction=%s", label)
    return {"priority": str(label), "model_version": version}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
