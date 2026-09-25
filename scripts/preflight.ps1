$ErrorActionPreference = 'Stop'

py -3.13 -m pip install -e '.[test]'
py -3.13 -m compileall -q src tests config.py
py -3.13 -m mypy
py -3.13 -m ruff check src tests
py -3.13 -m pytest -W error::ResourceWarning --cov=ai_trader --cov-fail-under=85
py -3.13 -m ai_trader.runtime --dry-run
py -3.13 -m ai_trader.evals.cli --dataset evals/scenarios/core.json --output evals/reports

Write-Output 'preflight=PASS'
