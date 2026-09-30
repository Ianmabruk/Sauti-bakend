# SautiPay Backend

Flask API for the Sauti AI assistant. Answers questions via a hosted language
model, grounds them with web research and citations, and serves marketplace and
news data to the frontend.

The frontend lives in a separate repository (Sauti.ai).

## Stack

| Concern | Choice |
| --- | --- |
| Framework | Flask |
| ORM | SQLAlchemy via Flask-SQLAlchemy |
| Migrations | Alembic (Flask-Migrate) |
| Database | SQLite by default, Postgres supported |
| Model provider | Groq, with Gemini and an offline fallback |
| HTTP client | `httpx` |
| Tests | pytest |

## Requirements

- Python 3.12
- A model provider key

## Setup

```bash
pip install -e ".[jarvis]"
cp .env.example .env
```

Then put your provider key in `.env`:

```
GROQ_API_KEY=...
```

`.env` is gitignored. Every setting has a default, so the app boots without
one — chat requests then fall back to a deterministic offline reply rather
than failing. `GET /api/sauti/health` reports which engine is live.

## Running

Run from the repository root: the `ai` package is imported at request time and
is not included in the installed distribution, so it must be importable.

```bash
FLASK_APP=wsgi.py python -m flask run --port 8000
# or
python wsgi.py
```

For production, use gunicorn. The target is `wsgi:app` — **not**
`backend.app:app`, because that module only exports `create_app` and never a
module-level `app`:

```bash
gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app
```

## Configuration

Settings resolve from the environment via `backend/config/settings.py`. The
ones that matter most:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | empty | Model provider key. Without it, chat is offline. |
| `GROQ_MODEL` | `qwen/qwen3.8-27b` | Must be reachable on your account. |
| `GROQ_MAX_TOKENS` | `900` | Must stay under your tier's output-tokens-per-minute limit. Exceeding it returns a 429 for the whole request, not a truncated answer. |
| `DATABASE_URL` | `sqlite:///sautipay.db` | Postgres in production. |
| `ADMIN_TOKEN` | empty | Required for `/api/admin/*`; those routes return 503 without it. |
| `ALLOWED_ORIGINS` | localhost:5173,3000 | CORS. Add the frontend's public origin in production. |
| `SEARCH_PROVIDER` | brave | Web search backend for grounded answers. |

Note `alembic.ini` ships with a Postgres URL that conflicts with the SQLite
default. Override it before running migrations against SQLite.

## API

45 routes across 12 blueprints. The ones the frontend depends on:

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/sauti/chat` | Ask Sauti. Reaches the model. |
| POST | `/api/sauti/transcribe` | Speech-to-text from a recorded clip. |
| GET | `/api/sauti/health` | Engine status: configured, reachable, model. |
| POST | `/api/chat` | Legacy chat route, same orchestrator. |
| GET | `/api/chat/history/<id>` | Full transcript for one conversation. |
| GET | `/api/health` | Liveness. |
| GET | `/api/languages` | Supported languages with native names. |
| POST | `/api/marketplace/search` | Product search. |
| GET | `/api/marketplace/news` | News feed, filterable by category. |
| GET | `/api/marketplace/categories` | Marketplace categories. |
| GET | `/api/marketplace/popular` | Most-searched terms. |
| GET/POST | `/api/marketplace/saved` | Bookmarked items. |
| GET | `/api/marketplace/vendors/featured` | Featured vendors. |

Admin routes require an `X-Admin-Token` header or a bearer token.

### Reading a chat response

`engine` names the **configured** provider, so it reads `"groq"` whether or not
groq answered. Use the outcome fields:

| Field | Meaning |
| --- | --- |
| `engine_ok` | `true` only when the model generated the reply |
| `degraded` | `true` whenever the reply is not a live answer |
| `engine_error` | Short safe reason; never contains provider internals |
| `offline_fallback` | The local composer produced the reply instead |

A degraded turn still returns HTTP 200, so clients must check `engine_ok` or
`degraded` rather than the status code.

## Layout

```
wsgi.py            WSGI entrypoint; the callable gunicorn loads
backend/
  app.py           Application factory
  config/          Settings dataclasses
  api/             Route blueprints
  agent/           Orchestrator, tool loops, memory, prompts
  services/        LLM, marketplace, research, memory
  tools/           Tool registry and implementations
  integrations/    Provider clients
  memory/          Long-term memory storage and retrieval
  schemas/         Request and response models
  migrations/      Alembic versions
ai/                Model provider factory, imported at request time
jarvis/            Separate voice CLI, not mounted in Flask
scripts/           Setup, seeding and diagnostics
tests/             pytest suite
```

## Tests

```bash
python -m pytest tests/ -q
```

The suite runs without network access: provider keys are stripped by an
autouse fixture and HTTP is mocked.

## Deploying to Render

`render.yaml` is a Render blueprint: connect the repo in Render, choose
**Blueprint**, and it provisions Postgres plus the web service with
`DATABASE_URL` wired in automatically.

| Variable | Must set | Notes |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | yes | The frontend's public origin, e.g. `https://sauti-ai.onrender.com`. Set after the frontend deploys. |
| `GROQ_API_KEY` | yes | From [console.groq.com](https://console.groq.com/keys). |
| `SEARCH_API_KEY` | for real research | Any `SEARCH_PROVIDER` other than `stub`. |

`ADMIN_TOKEN` and `SECRET_KEY` are generated for you. Tables are created on first
boot: `wsgi.py` calls `db.create_all()` at import time.

Deploy the **backend first**, then the frontend with this service's URL.

Two things to know:

- `alembic.ini` ships with a hardcoded Postgres URL that conflicts with the
  SQLite default. The blueprint sets `DATABASE_URL` for the app itself; run
  migrations with `flask db upgrade` rather than `alembic upgrade` if you need
  them.
- Free-tier disks do not exist, so uploaded media is lost on every restart. Add
  a `disk` to the blueprint and use a paid plan if you need uploads to persist.

### Other hosts

```bash
pip install -e .
gunicorn -w 2 -k gthread --threads 4 -b 0.0.0.0:$PORT wsgi:app
```

The target is `wsgi:app`, not `backend.app:app` — that module only exports
`create_app`, never a module-level `app`. The working directory must be the
repository root, because the `ai` package is imported at request time and is not
included in the installed distribution.
