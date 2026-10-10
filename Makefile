.DEFAULT_GOAL := help
.PHONY: help setup setup-all setup-python setup-typescript setup-rust check check-python check-typescript check-rust format format-python format-typescript format-rust run play play-ws play-python play-typescript play-rust

LANGUAGE ?= python
SETTINGS ?= secrets/player-settings.json
ARGS ?=
UV ?= uv
NODE ?= node
NPM ?= npm
CARGO ?= $(or $(shell command -v cargo 2>/dev/null),$(wildcard $(HOME)/.cargo/bin/cargo),cargo)
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
SETTINGS_FILE := $(abspath $(if $(filter /%,$(SETTINGS)),$(SETTINGS),$(ROOT)$(SETTINGS)))

ifeq ($(filter $(LANGUAGE),python typescript rust),)
$(error LANGUAGE must be python, typescript, or rust)
endif

help:
	@echo 'make setup|play LANGUAGE=python|typescript|rust [SETTINGS=file] [ARGS="--matches 3"]'
	@echo 'make setup-all, make check, make format; language-specific targets are also available'

setup: setup-$(LANGUAGE)
setup-all: setup-python setup-typescript setup-rust
setup-python:
	$(UV) sync --directory "$(ROOT)python" --locked
setup-typescript:
	$(NPM) --prefix "$(ROOT)typescript" ci --ignore-scripts
setup-rust:
	$(CARGO) build --locked --manifest-path "$(ROOT)rust/Cargo.toml"

check: check-python check-typescript check-rust
check-python:
	$(UV) run --directory "$(ROOT)python" --locked ruff check .
	$(UV) run --directory "$(ROOT)python" --locked ruff format --check .
	$(UV) run --directory "$(ROOT)python" --locked python main.py --help
	$(UV) run --directory "$(ROOT)python" --locked python -m unittest discover -s test -t . -v
check-typescript:
	$(NPM) --prefix "$(ROOT)typescript" run check
check-rust:
	$(CARGO) fmt --manifest-path "$(ROOT)rust/Cargo.toml" --check
	$(CARGO) clippy --locked --manifest-path "$(ROOT)rust/Cargo.toml" --all-targets -- -D warnings
	$(CARGO) test --locked --manifest-path "$(ROOT)rust/Cargo.toml"
	$(CARGO) run --locked --manifest-path "$(ROOT)rust/Cargo.toml" -- --help

format: format-python format-typescript format-rust
format-python:
	$(UV) run --directory "$(ROOT)python" --locked ruff format .
format-typescript:
	$(NPM) --prefix "$(ROOT)typescript" run format
format-rust:
	$(CARGO) fmt --manifest-path "$(ROOT)rust/Cargo.toml"

run play play-ws: play-$(LANGUAGE)
play-python:
	$(UV) run --directory "$(ROOT)python" --locked python main.py "$(SETTINGS_FILE)" --start $(ARGS)
play-typescript:
	$(NODE) "$(ROOT)typescript/main.ts" "$(SETTINGS_FILE)" --start $(ARGS)
play-rust:
	$(CARGO) run --locked --manifest-path "$(ROOT)rust/Cargo.toml" -- "$(SETTINGS_FILE)" --start $(ARGS)
