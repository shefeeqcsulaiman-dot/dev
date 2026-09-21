# Regenerates app.min.js from the current app.js on every build, so
# production always ships up-to-date minified JS. Previously this was a
# manual `npm run build:min` step nobody reliably re-ran after editing
# app.js, so the SHA-256 freshness check in app/main.py's _resolve_app_js()
# would silently fall back to serving the full unminified file — see that
# function and frontend/scripts/build-min.mjs for how the check works. That
# fallback now only matters as defense-in-depth, not the normal path.
#
# Also rewrites the ?v=... cache-busting query strings on app.js/styles.css
# in index.html/hrms.html (frontend/scripts/inject-asset-versions.mjs) from
# each asset's own content hash. Previously that version string was a
# hand-maintained date-ish string kept in sync across two files by hand —
# forgetting to bump it meant browsers that had already fetched the old
# ?v=... URL kept serving it forever (it's cached as immutable), even after
# the server had a newer file.
FROM node:20-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/scripts ./scripts
COPY frontend/public/taxflow ./public/taxflow
RUN npm run build:min && npm run inject-versions

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
# Sibling of backend/app/ — download_bridge_script()/download_report_script()
# (app/routers/attendance.py) look for these at .parent.parent.parent (i.e.
# /app here), matching where this COPY lands them. Previously only
# `backend/app` was copied into the image, so both endpoints 404'd in
# production ("Bridge script not found on server") despite the files
# existing in the repo and working from a local checkout the whole time.
COPY backend/zk_bridge.py ./zk_bridge.py
COPY backend/daily_attendance_report.py ./daily_attendance_report.py
COPY frontend/public ./frontend/public
COPY --from=frontend-build /build/public/taxflow/src/app.min.js ./frontend/public/taxflow/src/app.min.js
COPY --from=frontend-build /build/public/taxflow/index.html ./frontend/public/taxflow/index.html
COPY --from=frontend-build /build/public/taxflow/hrms.html ./frontend/public/taxflow/hrms.html
COPY --from=frontend-build /build/public/taxflow/ess.html ./frontend/public/taxflow/ess.html

EXPOSE 8000
# WORKERS defaults to 4; override with -e WORKERS=8 for higher-traffic deployments
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WORKERS:-4}
