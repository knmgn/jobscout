VENV ?= .venv
PY := $(VENV)/bin/python

.PHONY: install hooks lint test test-unit board check

$(PY):
	python3 -m venv $(VENV)

install: $(PY) hooks
	$(PY) -m pip install -q -e ".[dev]"
	$(PY) -m playwright install chromium

# Route commits through scripts/check_sanitized.sh.
hooks:
	git config core.hooksPath .githooks

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

test:
	$(PY) -m pytest

# Everything that does not need a browser.
test-unit:
	$(PY) -m pytest -m "not browser"

board:
	$(PY) -m jobscout.demo_board

check: lint test
	scripts/check_sanitized.sh
