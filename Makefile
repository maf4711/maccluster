.PHONY: verify lint format test install editable clean

verify: lint test

lint:
	ruff check src tests
	ruff format --check src tests

format:
	ruff check --fix src tests
	ruff format src tests

test:
	python -m pytest -q

install:
	python -m pip install -e ".[dev]"

editable: install

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
