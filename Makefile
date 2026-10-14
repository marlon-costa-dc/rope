VENV ?= .venv
PYTHON := $(VENV)/bin/python
BOOTSTRAP_PYTHON ?= python3.13

.PHONY: setup test test-file check fmt build

setup:
	$(BOOTSTRAP_PYTHON) -m venv "$(VENV)"
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest --cov -v -ra

test-file:
	test -n "$(FILE)"
	$(PYTHON) -m pytest -v -ra "$(FILE)"

check:
	$(PYTHON) -m pre_commit run --all-files

fmt:
	$(PYTHON) -m pre_commit run black --all-files
	$(PYTHON) -m pre_commit run isort --all-files

build:
	$(PYTHON) -m build
	$(PYTHON) -m ropetest.package_artifact_contract
