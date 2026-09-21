FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Explicit runtime inputs add a second boundary beyond .dockerignore.
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY tests/ ./tests/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "scripts.verify_data"]
