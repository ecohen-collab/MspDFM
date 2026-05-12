FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Paris

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dépendances Python figées
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Code source + frontend (image autonome pour Azure Container Apps)
# .dockerignore exclut .env, .venv, snapshots/, msp_data.json, etc.
COPY . /app

# Données runtime : volume mount Azure Files monté sur /data en prod.
# En local (compose), override met un bind sur ./data ou volume nommé.
ENV DATA_DIR=/data

EXPOSE 8080

# Healthcheck pour Container Apps liveness probe
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/index.html', timeout=3)" || exit 1

CMD ["python", "updater.py"]
