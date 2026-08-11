# Regenerates app.min.js from the current app.js on every build, so
# production always ships up-to-date minified JS. Previously this was a
# manual `npm run build:min` step nobody reliably re-ran after editing
# app.js, so the SHA-256 freshness check in app/main.py's _resolve_app_js()
# would silently fall back to serving the full unminified file — see that
# function and frontend/scripts/build-min.mjs for how the check works. That
# fallback now only matters as defense-in-depth, not the normal path.
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

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend/public ./frontend/public
COPY --from=frontend-build /build/public/taxflow/src/app.min.js ./frontend/public/taxflow/src/app.min.js

EXPOSE 8000
# WORKERS defaults to 4; override with -e WORKERS=8 for higher-traffic deployments
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WORKERS:-4}
