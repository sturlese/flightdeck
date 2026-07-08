# flightdeck — convenience targets.
.DEFAULT_GOAL := help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Editable install with dev tools
	pip install -e ".[dev]"

demo: ## Seeded 13-week demo org + executive dashboard (no API keys needed)
	flightdeck demo

test: ## Run the test suite (coverage gate 85%)
	python -m pytest --cov=flightdeck --cov-fail-under=85

lint: ## Ruff over src and tests
	ruff check src tests

.PHONY: help install demo test lint
