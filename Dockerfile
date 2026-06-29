# GTM Intelligence web app. Python 3.11 so the optional PyMC MMM backend can be
# installed later (the default laplace/metropolis backends work on 3.9+ too).
FROM python:3.11-slim

WORKDIR /app

# System deps for pandas/scipy wheels are bundled; keep image lean.
COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt

COPY . .

# Data (SQLite + parquet artifacts) lives on a mounted volume in production.
ENV PIPELINE_DATA_DIR=/data
EXPOSE 8000

# Railway provides $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn webapp.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
