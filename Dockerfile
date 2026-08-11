# See backend/Dockerfile for why this build stage exists — regenerates
# app.min.js from the current app.js on every build instead of relying on a
# manually-run `npm run build:min` that easily goes stale.
FROM node:20-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/scripts ./scripts
COPY frontend/public/taxflow/src/app.js ./public/taxflow/src/app.js
RUN npm run build:min

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps: tesseract for OCR, poppler for PDF-to-image
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ara \
    poppler-utils \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend/public ./frontend/public
COPY --from=frontend-build /build/public/taxflow/src/app.min.js ./frontend/public/taxflow/src/app.min.js

EXPOSE 8000
# WORKERS defaults to 2 — set to 4 on Render standard/pro plans (2+ vCPU)
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WORKERS:-2}
