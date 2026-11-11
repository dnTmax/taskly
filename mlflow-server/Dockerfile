FROM python:3.12-slim

RUN pip install --no-cache-dir \
    mlflow==2.22.5 \
    psycopg2-binary==2.9.9 \
    boto3==1.35.99 \
    sqlalchemy==2.0.36

# Utilisateur non root
RUN useradd --system --no-create-home --uid 10001 mlflow
USER mlflow

EXPOSE 5000
ENTRYPOINT ["mlflow", "server"]
