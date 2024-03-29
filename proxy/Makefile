# Bandit is a static code analysis tool to detect security vulnerabilities in Python applications
# https://wiki.openstack.org/wiki/Security/Projects/Bandit
.PHONY: all
all: help

.PHONY: bandit
bandit: ## Run bandit with medium level excluding test-related folders
	@echo "Running bandit security checks…"
	@poetry run bandit -ll --recursive securedrop_proxy

.PHONY: safety
safety: ## Runs `safety check` to check python dependencies for vulnerabilities
	@echo "Running safety against build requirements…"
	@poetry run safety check --full-report -r build-requirements.txt

.PHONY: lint
lint: check-isort check-black mypy ## Run isort, black and flake8 and mypy
	@poetry run flake8 securedrop_proxy tests

.PHONY: mypy
mypy: ## Run mypy static type checker
	@poetry run mypy --ignore-missing-imports securedrop_proxy

.PHONY: black
black: ## Run black for file formatting
	@echo "Running black (may result in changes in your working directory)…"
	@poetry run black securedrop_proxy tests

.PHONY: check-black
check-black: ## Check Python source code formatting with black
	@echo "Running black formatting check…"
	@poetry run black --check --diff securedrop_proxy tests

.PHONY: isort
isort: ## Run isort for file formatting
	@echo "Running isort (may result in changes in your working directory)…"
	@poetry run isort securedrop_proxy/*.py tests/*.py

.PHONY: check-isort
check-isort: ## Check isort for file formatting
	@echo "Running isort module ordering check…"
	@poetry run isort --check-only --diff securedrop_proxy/*.py tests/*.py

.PHONY: test
test: clean .coverage ## Runs tests with coverage

.coverage:
	@poetry run coverage run --source securedrop_proxy -m unittest

.PHONY: browse-coverage
browse-coverage: .coverage ## Generates and opens HTML coverage report
	@poetry run coverage html
	@xdg-open htmlcov/index.html 2>/dev/null || open htmlcov/index.html 2>/dev/null

.PHONY: check
check: clean lint test mypy safety bandit  ## Runs all tests and code checkers

.PHONY: clean
clean:  ## Clean the workspace of generated resources
	@rm -rf .mypy_cache build dist *.egg-info .coverage .eggs docs/_build .pytest_cache lib htmlcov .cache && \
		find . \( -name '*.py[co]' -o -name dropin.cache \) -delete && \
		find . \( -name '*.bak' -o -name dropin.cache \) -delete && \
		find . \( -name '*.tgz' -o -name dropin.cache \) -delete && \
		find . -name __pycache__ -print0 | xargs -0 rm -rf

# Explanation of the below shell command should it ever break.
# 1. Set the field separator to ": ##" and any make targets that might appear between : and ##
# 2. Use sed-like syntax to remove the make targets
# 3. Format the split fields into $$1) the target name (in blue) and $$2) the target descrption
# 4. Pass this file as an arg to awk
# 5. Sort it alphabetically
# 6. Format columns with colon as delimiter.
.PHONY: help
help: ## Print this message and exit.
	@printf "Makefile for developing and testing the SecureDrop proxy.\n"
	@printf "Subcommands:\n\n"
	@awk 'BEGIN {FS = ":.*?## "} /^[0-9a-zA-Z_-]+:.*?## / {printf "\033[36m%s\033[0m : %s\n", $$1, $$2}' $(MAKEFILE_LIST) \
		| sort \
		| column -s ':' -t
