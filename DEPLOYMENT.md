# DukaMate Backend — Deployment

This is a self-contained guide for getting DukaMate's backend running in
production. Three deployment shapes are documented:

1. **docker-compose** — easiest path; FastAPI + PostgreSQL + worker containers.
2. **Standalone Linux** — bare-metal / VM; FastAPI under uvicorn/gunicorn,
   workers under systemd timers.
3. **Cloud / Kubernetes** — outline only; the same images run in any orchestrator.

## Prerequisites

- PostgreSQL 16 (gen_random_uuid + RLS used)
- Python 3.12+
- A persistent disk for the database

## Required environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | yes | SQLAlchemy URL, e.g. `postgresql+asyncpg://dukapp:pass@db:5432/dukamate` |
| `JWT_SECRET_KEY` | yes | Symmetric secret for JWT signing. Long, random. |
| `JWT_ACCESS_TTL_MINUTES` | no | Default 30. |
| `JWT_REFRESH_TTL_DAYS` | no | Default 30. |
| `CORS_ORIGINS` | no | Comma-separated list of allowed front-end origins. |
| `LOG_LEVEL` | no | Default INFO. |

The `DATABASE_URL` user MUST be a non-superuser (so RLS applies). Create one:

```sql
-- run as the database owner
CREATE ROLE dukapp LOGIN PASSWORD '<strong>';
GRANT USAGE ON SCHEMA public TO dukapp;
GRANT ALL ON ALL TABLES IN SCHEMA public TO dukapp;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO dukapp;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO dukapp;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dukapp;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO dukapp;
```

## Initialising the schema

Apply Alembic migrations:

```bash
DATABASE_URL=postgresql+asyncpg://owner:pass@db:5432/dukamate alembic upgrade head
```

This creates every table (8 migrations), seeds every lookup, and creates the
dashboard views.

## 1. docker-compose deployment

The simplest production-realistic setup. Three services: db, api, worker.
The worker container runs both jobs on schedule (a tiny wrapper script
invokes the CLI entrypoints at fixed intervals).

```yaml
# docker-compose.yml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: dukamate
      POSTGRES_USER: dukaowner
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - dukamate_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U dukaowner"]
      interval: 5s

  migrate:
    build: .
    depends_on:
      db: { condition: service_healthy }
    environment:
      DATABASE_URL: postgresql+asyncpg://dukaowner:${POSTGRES_PASSWORD}@db:5432/dukamate
    command: alembic upgrade head

  api:
    build: .
    depends_on:
      migrate: { condition: service_completed_successfully }
    environment:
      DATABASE_URL: postgresql+asyncpg://dukapp:${DUKAPP_PASSWORD}@db:5432/dukamate
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
    ports: ["8000:8000"]
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

  worker:
    build: .
    depends_on:
      migrate: { condition: service_completed_successfully }
    environment:
      DATABASE_URL: postgresql+asyncpg://dukapp:${DUKAPP_PASSWORD}@db:5432/dukamate
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
    # Simple loop: issue engine every 15 min, reconcile at 02:15 UTC.
    # Replace with a real scheduler (cron, systemd timer, k8s CronJob) for prod.
    command: >
      sh -c "while true; do
        date;
        python -m app.workers.issue_engine || true;
        sleep 900;
      done"

  reconciliation:
    build: .
    depends_on:
      migrate: { condition: service_completed_successfully }
    environment:
      DATABASE_URL: postgresql+asyncpg://dukapp:${DUKAPP_PASSWORD}@db:5432/dukamate
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
    # Daily reconcile: run, then sleep 24h.
    command: >
      sh -c "while true; do
        sleep $((24*3600));
        python -m app.workers.reconciliation || true;
      done"

volumes:
  dukamate_pgdata:
```

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Bring it up:

```bash
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" > .env
echo "DUKAPP_PASSWORD=$(openssl rand -hex 16)"  >> .env
echo "JWT_SECRET_KEY=$(openssl rand -hex 32)"   >> .env
# create the dukapp role inside the db container, e.g.:
docker compose up -d db
docker compose exec db psql -U dukaowner -d dukamate -c \
  "CREATE ROLE dukapp LOGIN PASSWORD '<read from .env>'; \
   GRANT USAGE ON SCHEMA public TO dukapp; \
   GRANT ALL ON ALL TABLES IN SCHEMA public TO dukapp; \
   GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO dukapp; \
   GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO dukapp;"
docker compose up -d migrate api worker reconciliation
```

## 2. Standalone Linux deployment

For a VM running PostgreSQL locally:

```bash
# api unit
# /etc/systemd/system/dukamate-api.service
[Unit]
Description=DukaMate API
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=dukamate
WorkingDirectory=/opt/dukamate
Environment=DATABASE_URL=postgresql+asyncpg://dukapp:****@localhost/dukamate
Environment=JWT_SECRET_KEY=<from-secret-store>
ExecStart=/opt/dukamate/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Front this with nginx/caddy for TLS.

Workers run on a timer, not as a daemon:

```ini
# /etc/systemd/system/dukamate-issue-engine.service
[Unit]
Description=DukaMate Issue Engine sweep
[Service]
Type=oneshot
User=dukamate
WorkingDirectory=/opt/dukamate
EnvironmentFile=/etc/dukamate/env
ExecStart=/opt/dukamate/.venv/bin/python -m app.workers.issue_engine

# /etc/systemd/system/dukamate-issue-engine.timer
[Unit]
Description=Run DukaMate Issue Engine every 15 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true
[Install]
WantedBy=timers.target
```

Same pattern for `dukamate-reconciliation` with `OnCalendar=*-*-* 02:15:00`.

Equivalent cron entries:

```cron
*/15 * * * *  cd /opt/dukamate && .venv/bin/python -m app.workers.issue_engine
15 2 * * *    cd /opt/dukamate && .venv/bin/python -m app.workers.reconciliation
```

## 3. Kubernetes outline

- One `Deployment` + `Service` for the API (uvicorn behind a Service).
- Two `CronJob`s: issue engine on `*/15 * * * *`, reconciliation on `15 2 * * *`.
- Postgres via a managed service (RDS / Cloud SQL / Aiven) or a stateful set.
- Secrets in `Secret` resources; mount as env vars.
- HorizontalPodAutoscaler on the API by CPU/RPS once traffic justifies it.

## Healthchecks

- Liveness: `GET /` returns the welcome JSON.
- DB readiness: `alembic current` exits 0 once migrations are applied.

## Operational notes

- **Workers + RLS**: the workers call DB functions that read under RLS. The
  worker code sets `app.current_business_id` per business via
  `set_config()` before running any reads. Don't change that unless you also
  redeclare `reconcile_business_cash` as `SECURITY DEFINER`.
- **Migrations**: every release runs `alembic upgrade head` before starting the
  API. The compose file's `migrate` service does this; on standalone, add a
  pre-start `ExecStartPre=` line to the api unit.
- **Backups**: standard PostgreSQL backups. Logical (pg_dump) is fine for
  small businesses; switch to PITR (pg_basebackup + WAL archiving) once you
  have customers.
- **Cron retry**: the workers are idempotent — the issue engine reaches a
  convergent state, and reconcile is a from-scratch recompute. A missed run is
  safely caught up by the next one.

## Smoke test after deployment

```bash
# create a user
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"phone_number":"+256700000001","password":"StrongPass123"}'

# log in
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"phone_number":"+256700000001","password":"StrongPass123","device_fingerprint":"smoke","platform":"ANDROID"}' \
  | jq -r .access_token)

# create a business
curl -X POST http://localhost:8000/api/v1/businesses \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"shop_name":"Smoke Test Shop","business_type_id":1,"currency_code":"UGX","opening_balance":"100000"}'
```

If those three succeed, every layer is wired: DB, RLS context, JWT, FastAPI
routing, schema migrations.
