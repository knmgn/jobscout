VENV ?= .venv
PY := $(VENV)/bin/python

.PHONY: install hooks lint test test-unit board demo check

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

# The whole pipeline against the demo board, from a clean state each time.
# No accounts needed; any OPENAI_API_KEY / Slack credentials that are set get used.
demo:
	rm -rf out/demo.db out/slack
	$(PY) -m jobscout --demo

check: lint test
	scripts/check_sanitized.sh
