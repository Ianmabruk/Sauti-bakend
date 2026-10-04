FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
# wsgi.py holds the module-level `app` callable that gunicorn loads, and `ai`
# is imported at request time by backend/agent/orchestrator.py and
# backend/services/llm.py. Neither is included in the installed distribution,
# so both must be copied explicitly or the app fails to boot in the container.
COPY wsgi.py ./
COPY ai ./ai
COPY backend ./backend

RUN pip install --no-cache-dir -e .

EXPOSE 8000

# `backend.app:app` does not exist: that module only exports `create_app`.
# The WSGI callable lives in wsgi.py.
#
# PORT comes from the platform. Render, Heroku and most PaaS providers set it,
# and the app must bind that exact value or the health check never passes and
# traffic is refused. gthread workers are used because the orchestrator calls
# asyncio.run() from synchronous request handling, which needs a real thread
# rather than a blocking worker loop.
ARG PORT=8000
ENV PORT=${PORT}
CMD ["sh", "-c", "gunicorn -w 4 -k gthread --threads 10 -b 0.0.0.0:${PORT} --timeout 120 wsgi:app"]