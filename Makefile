VENV := $(abspath $(CURDIR)/../.rope-venvs/$(notdir $(CURDIR)))
PYTHON := $(VENV)/bin/python
UV ?= uv

.PHONY: setup test test-file check fmt build

setup:
	$(UV) venv --python 3.13 "$(VENV)"
	$(UV) pip install --python "$(PYTHON)" -e '.[dev]'

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
