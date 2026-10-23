from __future__ import annotations

import pytest

from app.agents import SpecialistRunner
from app.platform import PlatformError
from app.sources import OfflineSource
from app.storage import Store
from app.workflow import Workflow


class Provider:
    last_usage = {"total_tokens": 1}

    def __init__(self, steps):
        self.steps = iter(steps)
        self.calls = 0

    def complete(self, *args):
        self.calls += 1
        value = next(self.steps)
        if isinstance(value, Exception):
            raise value
        return value


def runner(tmp_path, steps):
    store = Store(tmp_path / "agents.db")
    wf = Workflow(store)
    run = wf.create(mode="live")
    source = OfflineSource()
    snapshot = source.load_snapshot()
    provider = Provider(steps)
    instance = SpecialistRunner(store, source, provider, wf.event, wf.reserve)
    return instance, run, snapshot, provider


def tool(name):
    return {"action": "tool", "tool": name}


def submit():
    return {"action": "submit", "citations": ["DOC-DELAY"]}


def test_known_completed_invalid_contract_repairs_once(tmp_path):
    r, run, snapshot, p = runner(
        tmp_path, [PlatformError("INVALID_INFERENCE_RESPONSE"), tool("material_balance"), submit()]
    )
    result = r.invoke(run["id"], "operations", snapshot, "live")
    assert result["usage"] == [{"total_tokens": 1}] * 3
    assert p.calls == 3
    with r.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM receipts WHERE status='pending'").fetchone()[0] == 0


def test_one_repair_limit(tmp_path):
    r, run, snapshot, p = runner(tmp_path, [{}, {}])
    with pytest.raises(ValueError, match="invalid constrained output"):
        r.invoke(run["id"], "operations", snapshot, "live")
    assert p.calls == 2


def test_tool_allowlist_and_invocation_limit(tmp_path):
    r, run, snapshot, p = runner(tmp_path, [tool("raw_sql"), tool("raw_sql")])
    with pytest.raises(ValueError, match="outside its role"):
        r.invoke(run["id"], "operations", snapshot, "live")
    r, run, snapshot, p = runner(tmp_path, [tool("material_balance")] * 8)
    with pytest.raises(ValueError, match="eight-turn limit"):
        r.invoke(run["id"], "operations", snapshot, "live")
    assert p.calls == 8


def test_uncertain_transport_receipt_is_not_repeated(tmp_path):
    r, run, snapshot, p = runner(tmp_path, [PlatformError("CORTEX_UNAVAILABLE")])
    with pytest.raises(PlatformError):
        r.invoke(run["id"], "operations", snapshot, "live")
    with pytest.raises(ValueError, match="uncertain completion"):
        r.invoke(run["id"], "operations", snapshot, "live")
    assert p.calls == 1


def test_completed_receipts_replay_without_budget_recharge(tmp_path, monkeypatch):
    r, run, snapshot, p = runner(tmp_path, [tool("material_balance"), submit()])
    original = r.store.mutate
    failed = False

    def fail_cache(run_id, mutation):
        nonlocal failed
        import copy

        draft = copy.deepcopy(r.store.get(run_id))
        mutation(draft)
        if draft.get("role_cache") and not failed:
            failed = True
            raise RuntimeError("process interruption before cache commit")
        return original(run_id, mutation)

    monkeypatch.setattr(r.store, "mutate", fail_cache)
    with pytest.raises(RuntimeError):
        r.invoke(run["id"], "operations", snapshot, "live")
    before = r.store.get(run["id"])["budget"].copy()
    r.invoke(run["id"], "operations", snapshot, "live")
    assert r.store.get(run["id"])["budget"] == before
    assert p.calls == 2


def test_single_agent_can_parallelize_same_tools(tmp_path):
    r, run, snapshot, p = runner(
        tmp_path,
        [
            {
                "action": "parallel_tools",
                "tools": ["material_balance", "source_candidates", "search_evidence"],
            },
            tool("independent_qualification"),
            submit(),
        ],
    )
    result = r.invoke(run["id"], "single", snapshot, "live")
    assert set(result["tools"]) == {
        "material_balance",
        "source_candidates",
        "search_evidence",
        "independent_qualification",
    }
    assert p.calls == 3


def test_qualification_reviews_prefetched_independent_tools_once(tmp_path):
    r, run, snapshot, p = runner(tmp_path, [submit()])
    result = r.invoke(run["id"], "qualification", snapshot, "live")
    assert set(result["tools"]) == {"independent_qualification", "search_evidence"}
    assert p.calls == 1
    assert r.store.get(run["id"])["budget"]["tool_calls"] == 2
