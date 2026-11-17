# Development workflow

## Prerequisites

- Python 3.12.
- PostgreSQL 18, either local or the optional loopback-only Compose service.
- Git. Ruff is a separate development tool; the runtime/test libraries are in
  `requirements.txt`.

## Local setup

1. Create a virtual environment and install `requirements.txt`.
2. Copy `.env.example` to `.env` only for local Compose interpolation and
   replace its placeholder password. Do not commit `.env`.
3. Start PostgreSQL with `docker compose -f docker-compose.dev.yml up -d
   postgres` if using Compose.
4. Export `CONTROLLER_DATABASE_URL` in the shell used to start the Controller.
   The Python process reads environment variables; it does not load `.env`.
5. Apply migrations with `python -m alembic -c alembic.ini upgrade head`.
6. Start a compatible local service separately, then run
   `python -m controller <SERVICE_URL>`.

Platform-specific commands and troubleshooting are in the root README.

## Change workflow

Keep HTTP contracts in `controller/clients/`, database operations in
`controller/storage/`, and KPI logic in `controller/analytics/`. Preserve
explicit transaction boundaries and test failure/replay behavior when changing
durable workflows. Do not log credentials or request bodies. Run the narrowest
relevant test first, then the full normal suite and checks. Schema changes
require an Alembic revision and upgrade/downgrade verification against an
isolated PostgreSQL database.

This repository has no contribution policy, code-owner configuration, release
automation, or required CI workflow defined yet.
