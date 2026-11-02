# One image for the API, the worker and the seeder; compose picks the command.
FROM python:3.12-slim

WORKDIR /app
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

# The database lives on a volume, not in the source folder (see compose.yaml).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DUNNING_DB=/data/dunning.db

EXPOSE 8006
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8006"]
