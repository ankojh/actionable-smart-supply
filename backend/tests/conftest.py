"""Test-only access to retired fixtures for historical contract regressions.

NorthStar tests use active data unchanged. Production never reads this archive.
"""
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def legacy_fixture_paths(request, monkeypatch):
    if request.node.path.name in {'test_northstar.py', 'test_restart.py'}:
        return
    repo = Path(__file__).resolve().parents[2]
    archive = repo / 'archive_data/pre_northstar_happy_path/backend'
    redirects = {
        repo / 'backend/data/initial_case.json': archive / 'data/initial_case.json',
        repo / 'backend/demo/supplier_replies.json': archive / 'demo/supplier_replies.json',
        repo / 'backend/evaluator_data/realized_events.json': archive / 'evaluator_data/realized_events.json',
        repo / 'snowflake/load/initial_case.json': archive.parent / 'snowflake/load/initial_case.json',
        repo / 'snowflake/load/supplier_confirmations.json': archive.parent / 'snowflake/load/supplier_confirmations.json',
    }
    original = Path.read_text

    def read_fixture(path, *args, **kwargs):
        return original(redirects.get(path.resolve(), path), *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read_fixture)
