.PHONY: setup run test report serve lint clean e2e

PYTHON ?= python
VENV ?= .venv

ifeq ($(OS),Windows_NT)
  BIN := $(VENV)/Scripts
  PY := $(BIN)/python.exe
  PIP := $(BIN)/pip.exe
else
  BIN := $(VENV)/bin
  PY := $(BIN)/python
  PIP := $(BIN)/pip
endif

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt
	$(PIP) install -e .

lint:
	$(PY) -m ruff check src tests

test:
	$(PY) -m pytest -q

run:
	$(PY) -m mdif run --config config/pipeline.yaml

report:
	$(PY) -m mdif report --out docs

serve:
	$(PY) -m http.server 8000 --directory docs

e2e: setup run report

clean:
	rm -rf data/raw/* data/quarantine/* data/state/* docs/report.json docs/index.html || true
