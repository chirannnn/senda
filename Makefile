PYTHON ?= python

.PHONY: test unit contract integration lint format-check compile migrations-check

test:
	$(PYTHON) -m pytest -q tests

unit:
	$(PYTHON) -m pytest -q tests/unit

contract:
	$(PYTHON) -m pytest -q tests/contract

# Requires CONTROLLER_TEST_DATABASE_URL to name a disposable PostgreSQL database.
integration:
	$(PYTHON) -m pytest -q tests/integration

lint:
	ruff check .

format-check:
	ruff format --check .

compile:
	$(PYTHON) -m compileall -q controller tests

# Requires CONTROLLER_DATABASE_URL for the selected local development database.
migrations-check:
	$(PYTHON) -m alembic -c alembic.ini check
