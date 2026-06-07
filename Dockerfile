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

EXPOSE 8000
# WORKERS defaults to 2 — set to 4 on Render standard/pro plans (2+ vCPU)
CMD uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-2}
