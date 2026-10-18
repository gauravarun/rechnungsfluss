.PHONY: install lint format typecheck test check

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
