# syntax=docker/dockerfile:1
# Build context: REPO ROOT (not workers/screener/).
# CI command: docker build -f workers/screener/Dockerfile -t <image> .
#
# The backend services are copied into /app/backend so the screener worker
# can import `from services.X import Y` exactly as FastAPI does at runtime.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

# Install backend deps first (layer cache friendly).
COPY backend/requirements.txt /tmp/backend_requirements.txt
RUN pip install --no-cache-dir -r /tmp/backend_requirements.txt

# Install worker-specific deps.
COPY workers/screener/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend package (services, limiter, etc.) into /app/backend.
COPY backend/ /app/backend/

# Copy the worker itself.
COPY workers/screener/ /app/

# Set PYTHONPATH so `from services.X import Y` resolves to /app/backend/services/X.
ENV PYTHONPATH=/app/backend

CMD ["python", "-u", "main.py"]
