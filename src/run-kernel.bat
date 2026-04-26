@echo off
:: Start mustang kernel in dev mode (auto-reload).
cd /d "%~dp0kernel"
uv run python -m kernel --port 8200 --dev
