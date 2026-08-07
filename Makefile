.PHONY: help install run cli test test-fast lint clean

help:
	@echo "make install    install everything into .venv"
	@echo "make run        start the app on http://localhost:8000"
	@echo "make test       run the full suite (slow: renders real video)"
	@echo "make test-fast  schema + matcher only (seconds)"
	@echo "make lint       ruff"
	@echo "make clean      remove venv, database and generated media"

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
PKGS := services/common services/matcher services/analyzer services/indexer \
        services/renderer services/cli services/api
PYPATH := services/common:services/matcher:services/analyzer:services/indexer:services/renderer:services/cli:services/api

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet $(foreach p,$(PKGS),-e $(p))

install: $(VENV)

run: install
	@./run.sh

cli: install
	@echo "usage: $(PY) -m reelsedits_cli.main build reference.mp4 clips/ -o out.mp4"

test-fast:
	PYTHONPATH=$(PYPATH) python3 -m pytest services/common/tests services/matcher/tests -q

test:
	PYTHONPATH=$(PYPATH) python3 -m pytest \
	  services/common/tests services/matcher/tests \
	  services/analyzer/tests services/api/tests -q

lint:
	ruff check services/ schemas/ tools/

clean:
	rm -rf $(VENV) data reelsedits.db *.egg-info
	find services -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
