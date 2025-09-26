.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help`
help:
	@grep -E \
		'^.PHONY: .*?## .*$$' $(MAKEFILE_LIST) | \
		sort | \
		awk 'BEGIN {FS = ".PHONY: |## "}; {printf "\033[36m%-16s\033[0m %s\n", $$2, $$3}'


.PHONY: install ## install required dependencies on bare metal
install:
	uv sync


.PHONY: format ## Run the formatter on bare metal
format:
	uv run tox -e format


.PHONY: lint ## run the linter on bare metal
lint:
	uv run tox -e lint
