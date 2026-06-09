FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
# WORKERS defaults to 4; override with -e WORKERS=8 for higher-traffic deployments
CMD uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-4}
