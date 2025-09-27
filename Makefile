.DEFAULT_GOAL := help
SHELL := /bin/bash


.PHONY: install ## install required dependencies on bare metal
install:
	uv sync


.PHONY: format ## Run the formatter on bare metal
format:
	uv run tox -e format


.PHONY: lint ## run the linter on bare metal
lint:
	uv run tox -e lint
