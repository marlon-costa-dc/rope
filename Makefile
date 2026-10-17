VENV := $(shell dirname "$(CURDIR)")/.rope-venvs/$(shell basename "$(CURDIR)")
PYTHON := $(VENV)/bin/python
UV ?= uv
BOOTSTRAP_PYTHON ?= 3.13
export PYTHONDONTWRITEBYTECODE :=
export PYTHONPYCACHEPREFIX := $(VENV)/pycache
export COVERAGE_FILE := $(VENV)/.coverage

.PHONY: setup test test-file check fmt build

setup:
	@set -eu; \
	expected_python="$$( $(UV) python find --system "$(BOOTSTRAP_PYTHON)" )"; \
	if [ -e "$(VENV)" ] || [ -L "$(VENV)" ]; then \
		if [ -L "$(VENV)" ] || [ ! -x "$(PYTHON)" ] || [ ! -f "$(VENV)/pyvenv.cfg" ]; then \
			printf 'ERROR: existing environment is not a physical venv: %s\n' "$(VENV)" >&2; \
			exit 2; \
		fi; \
		if ! "$(PYTHON)" -c 'from pathlib import Path; import sys; sys.exit(0 if Path(sys.executable).resolve() == Path(sys.argv[1]).resolve() else 2)' "$$expected_python"; then \
			printf 'ERROR: existing environment uses another Python interpreter: %s\n' "$(VENV)" >&2; \
			exit 2; \
		fi; \
	else \
		$(UV) venv --python "$$expected_python" "$(VENV)"; \
	fi
	$(UV) pip install --python "$(PYTHON)" -e '.[dev]'

test:
	"$(PYTHON)" -m pytest --cov -v -ra -o cache_dir="$(VENV)/pytest-cache"

test-file:
	test -n "$(FILE)"
	"$(PYTHON)" -m pytest -v -ra -o cache_dir="$(VENV)/pytest-cache" "$(FILE)"

check:
	"$(PYTHON)" -m pre_commit run --all-files

fmt:
	"$(PYTHON)" -m pre_commit run black --all-files
	"$(PYTHON)" -m pre_commit run isort --all-files

build:
	"$(PYTHON)" -m build
	"$(PYTHON)" -m ropetest.package_artifact_contract
