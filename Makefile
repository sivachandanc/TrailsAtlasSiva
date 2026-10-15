DB := internal_core/clinical_trials.duckdb

.PHONY: help ui ui-readonly fetch trial-icd web test

help:  ## List available commands
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

# DuckDB allows one read-write process OR several read-only ones, never both:
# close the UI (either mode) before running `make fetch` or `make trial-icd`.

ui:  ## Open the DuckDB UI on the database
	duckdb -ui $(DB)

ui-readonly:  ## Open the DuckDB UI read-only (other read-only tools can open the file too)
	duckdb -ui -readonly $(DB)

fetch:  ## Fetch trials from ClinicalTrials.gov and save them to the database
	uv run python -m internal_core.explore_ctgov

trial-icd:  ## Build one trial's ICD prefix list, e.g. make trial-icd NCT=NCT03570632
	@test -n "$(NCT)" || (echo "Usage: make trial-icd NCT=NCT03570632" && exit 1)
	uv run python -m internal_core.trial_icd $(NCT)

web:  ## Run the trial search web app at http://127.0.0.1:8000 (reloads on code changes)
	uv run fastapi dev webapp/backend/main.py

test:  ## Run the web app tests
	uv run pytest tests
