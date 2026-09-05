FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

# Render (and most PaaS hosts) inject PORT at runtime; 5050 is the local-dev
# fallback. --timeout 120 and gthread workers matter here specifically
# because replies stream (SSE) — a sync worker's default timeout logic
# doesn't play well with a single request that stays open while tokens
# trickle in.
ENV PORT=5050
EXPOSE 5050
CMD ["sh", "-c", "gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT} app:app"]
