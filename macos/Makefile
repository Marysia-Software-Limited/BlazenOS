# macos/Makefile — rachel (macOS) audiobook agent.
# Phase A (Python render pipeline) + Phase B (rachel-player, Rust) live here.
AGENT := agent
VENV  := $(AGENT)/.venv
PY    := $(VENV)/bin/python

.PHONY: venv test lint render list clean

venv: ## Create the agent venv and install rachel + the shared domains lib
	cd $(AGENT) && python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ../domains/audiobook-catalog
	$(PY) -m pip install -q -e '$(AGENT)[azure,dev]'

test: ## Run the rachel unit suite
	cd $(AGENT) && .venv/bin/python -m pytest -q

lint: ## ruff on the agent sources
	cd $(AGENT) && .venv/bin/ruff check src tests

list: ## List Polish Calibre books
	cd $(AGENT) && .venv/bin/python -m rachel.cli list

render: ## Render a book: make render BOOK="metro 2033"
	cd $(AGENT) && .venv/bin/python -m rachel.cli render $(BOOK)

clean:
	rm -rf $(VENV) $(AGENT)/src/*.egg-info $(AGENT)/.pytest_cache
