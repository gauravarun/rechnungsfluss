.PHONY: install lint format typecheck test check web-install web-check

UV ?= uv

install:
	$(UV) sync --frozen

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck:
	$(UV) run mypy

test:
	$(UV) run pytest --cov=rechnungsfluss --cov-report=term-missing

check: lint typecheck test

web-install:
	cd web && npm ci --no-audit --no-fund

web-check:
	cd web && npm run lint && npm run typecheck && npm test && npm run build
