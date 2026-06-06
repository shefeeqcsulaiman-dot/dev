# TaxFlow Full Stack

TaxFlow is a React + FastAPI application with PostgreSQL, Redis/Celery, and S3-compatible storage.

## Run With Docker

```bash
docker compose up --build
```

Open:

- Frontend: http://localhost:5173
- API docs: http://localhost:8000/docs
- MinIO console: http://localhost:9001

Default login:

- Email: `admin@taxflowapp.com`
- Password: `admin123`

## Local Backend Without Docker

For a lightweight Windows run, copy `backend/.env.local` to `backend/.env` and run FastAPI from the `backend` folder:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If port `8000` is unavailable, run the backend on another port and point Vite at it:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8010
```

```env
VITE_API_BASE_URL=http://127.0.0.1:8010/api/v1
```

This local mode uses SQLite, local file storage, and eager Celery tasks. Docker Compose remains the production-like Postgres/Redis/S3 stack.

## Project Layout

```text
backend/                   FastAPI API, SQLAlchemy models, Celery worker, S3 storage
frontend/                  React + Vite shell
frontend/public/taxflow/   TaxFlow UI assets mounted by the React shell
docs/                      Architecture notes
testing/                   Testing rules, module guides, and test matrix
```

## Testing Guidance

TaxFlow testing rules and module-specific QA prompts live in `testing/`.

Start with:

- `testing/TESTING_RULES.md`
- `testing/TEST_MATRIX.md`
