.PHONY: help deploy update logs test lint typecheck check

HA_HOST ?= homeassistant.local
HA_USER ?= root
ADDON_SLUG := local_ha_ops_mcp

help:
	@echo "ha-ops-mcp — development targets"
	@echo ""
	@echo "  make deploy      Push code to HA via SCP (no rebuild)"
	@echo "  make update      Push code + store reload + ha apps update (preserves config)"
	@echo "  make logs        Tail addon logs via SSH"
	@echo "  make check       Run ruff + mypy + pytest"
	@echo "  make test        Run pytest only"
	@echo "  make lint        Run ruff only"
	@echo "  make typecheck   Run mypy --strict only"

deploy:
	./scripts/dev-deploy.sh

update:
	@echo "▶ Syncing latest source..."
	./scripts/dev-deploy.sh
	@echo "▶ Rescanning app store..."
	ssh $(HA_USER)@$(HA_HOST) "ha store reload"
	@echo "▶ Applying update (preserves configuration)..."
	ssh $(HA_USER)@$(HA_HOST) "ha apps update $(ADDON_SLUG)"
	@echo "✓ Update complete — v$$(awk -F'"' '/^version:/{print $$2}' config.yaml)"

logs:
	ssh $(HA_USER)@$(HA_HOST) "ha apps logs $(ADDON_SLUG)"

test:
	.venv/bin/pytest tests/ -v

lint:
	.venv/bin/ruff check src/ tests/

typecheck:
	.venv/bin/mypy src/ha_ops_mcp --strict

check: lint typecheck test
