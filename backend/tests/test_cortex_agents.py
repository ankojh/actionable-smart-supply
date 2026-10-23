"""Native Agent orchestration and callback isolation, without cloud I/O."""

import copy

import pytest

from app.agents import SpecialistRunner
from app.config import Settings
from app.cortex import agent_spec
from app.platform import PlatformError, SnowflakePlatform
from app.sources import OfflineSource
from app.storage import Store
from app.workflow import Workflow


THREAD = "9223372036854770001"


def tool(name, **changes):
    return {"type": "tool_use", "tool_use": {
        "tool_use_id": "tool-" + name, "name": name, "type": "generic",
        "client_side_execute": True, "input": {"query": "DRV-24-B"}, **changes,
    }}


class NativeProvider:
    native_agents = True

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.thread_calls = 0

    def create_agent_thread(self):
        self.thread_calls += 1
        return THREAD

    def run_agent(self, role, thread, parent, content):
        self.calls.append((role, thread, parent, copy.deepcopy(content)))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return {"content": response, "metadata": {
            "thread_id": THREAD, "assistant_message_id": str(100 + len(self.calls)),
            "run_id": "native-run-" + str(len(self.calls)), "request_id": "request-" + str(len(self.calls)),
            "usage": {"tokens_consumed": []},
        }}


def make_runner(tmp_path, responses):
    store = Store(tmp_path / "native.db")
    workflow = Workflow(store)
    run = workflow.create(mode="live")
    source = OfflineSource()
    provider = NativeProvider(responses)
    runner = SpecialistRunner(store, source, provider, workflow.event, workflow.reserve)
    return runner, run, source.load_snapshot(), provider


def test_native_round_trip_records_exact_execution_and_bound_callback_results(tmp_path):
    runner, run, snapshot, provider = make_runner(tmp_path, [
        [tool("material_balance")], [{"type": "text", "text": "Reviewed the inventory evidence."}],
    ])
    finding = runner.invoke(run["id"], "operations", snapshot, "live")
    assert finding["provider"] == "Snowflake Cortex Agent"
    assert finding["execution"]["thread_id"] == THREAD
    assert finding["execution"]["status"] == "COMPLETED"
    assert finding["tools"] == ["material_balance"]
    callback = provider.calls[1][3][0]["tool_result"]
    assert callback["tool_use_id"] == "tool-material_balance"
    assert callback["content"][0]["json"]["required_drivers"] == 300
    assert provider.calls[1][2] == "101"
    saved = runner.store.get(run["id"])
    assert saved["agent_executions"]["operations"][0] == finding["execution"]
    assert saved["budget"]["model_turns"] == 2
    assert saved["budget"]["tool_calls"] == 1
    assert runner.invoke(run["id"], "operations", snapshot, "live") == finding
    assert provider.thread_calls == 1 and len(provider.calls) == 2


@pytest.mark.parametrize("requested,match", [
    ([tool("raw_sql")], "unregistered"),
    ([tool("material_balance", input={"company": "another-company"})], "bound scope"),
    ([tool("material_balance", client_side_execute=False)], "server-side"),
    ([{"type": "text", "text": "I did not use any tools."}], "required tools"),
    ([tool("material_balance"), tool("material_balance")], "duplicate"),
])
def test_native_tools_fail_closed_before_any_callback(tmp_path, requested, match):
    runner, run, snapshot, provider = make_runner(tmp_path, [requested])
    with pytest.raises(ValueError, match=match):
        runner.invoke(run["id"], "operations", snapshot, "live")
    assert runner.store.get(run["id"])["budget"]["tool_calls"] == 0


def test_native_transport_uncertainty_is_never_retried_or_fallback(tmp_path):
    runner, run, snapshot, provider = make_runner(tmp_path, [PlatformError("CORTEX_UNAVAILABLE")])
    with pytest.raises(PlatformError):
        runner.invoke(run["id"], "operations", snapshot, "live")
    with pytest.raises(ValueError, match="uncertain completion"):
        runner.invoke(run["id"], "operations", snapshot, "live")
    assert provider.thread_calls == 1 and len(provider.calls) == 1


def test_native_completed_receipts_survive_interruption_before_finding_cache(tmp_path, monkeypatch):
    runner, run, snapshot, provider = make_runner(tmp_path, [
        [tool("material_balance")], [{"type": "text", "text": "Reviewed."}],
    ])
    original = runner.store.mutate
    failed = False

    def interrupt_cache(run_id, mutation):
        nonlocal failed
        draft = copy.deepcopy(runner.store.get(run_id))
        mutation(draft)
        if draft.get("role_cache") and not failed:
            failed = True
            raise RuntimeError("simulated process interruption")
        return original(run_id, mutation)

    monkeypatch.setattr(runner.store, "mutate", interrupt_cache)
    with pytest.raises(RuntimeError):
        runner.invoke(run["id"], "operations", snapshot, "live")
    budget = runner.store.get(run["id"])["budget"].copy()
    finding = runner.invoke(run["id"], "operations", snapshot, "live")
    assert finding["execution"]["status"] == "COMPLETED"
    assert len(provider.calls) == 2 and provider.thread_calls == 1
    assert runner.store.get(run["id"])["budget"] == budget
    assert len(runner.store.get(run["id"])["agent_executions"]["operations"]) == 1


def test_native_single_qualification_requires_returned_dependencies(tmp_path):
    runner, run, snapshot, provider = make_runner(tmp_path, [[tool("independent_qualification")]])
    with pytest.raises(ValueError, match="dependencies"):
        runner.invoke(run["id"], "single", snapshot, "live")
    assert runner.store.get(run["id"])["budget"]["tool_calls"] == 0


def test_native_provider_targets_persistent_object_and_preserves_large_identifiers():
    captured = []
    settings = Settings(_env_file=None, user="test", database="TEST_DB")

    def rest(path, body):
        captured.append((path, body))
        if path.endswith("/threads"):
            return {"thread_id": int(THREAD)}
        return {"content": [{"type": "text", "text": "Reviewed."}], "metadata": {
            "thread_id": int(THREAD), "assistant_message_id": 9223372036854770002,
        }}

    platform = SnowflakePlatform(settings, rest_fn=rest)
    thread = platform.create_agent_thread()
    assert thread == THREAD
    result = platform.run_agent("operations", thread, "0", [{"type": "text", "text": "Investigate"}])
    assert captured[1][0] == "/api/v2/databases/TEST_DB/schemas/SUPPLY_RECOVERY_V1/agents/SUMO_OPERATIONS_V1:run"
    assert captured[1][1]["thread_id"] == int(THREAD)
    assert captured[1][1]["stream"] is False
    assert result["metadata"]["assistant_message_id"] == "9223372036854770002"
    assert agent_spec("operations", "claude-sonnet-4-6")["tools"][0]["tool_spec"]["name"] == "material_balance"


@pytest.mark.parametrize("metadata", [
    {}, {"assistant_message_id": 1, "thread_id": 42}, {"assistant_message_id": "not-an-id"},
])
def test_native_response_rejects_missing_or_cross_thread_metadata(metadata):
    settings = Settings(_env_file=None, user="test", database="TEST_DB")
    platform = SnowflakePlatform(settings, rest_fn=lambda path, body: {"content": [], "metadata": metadata})
    with pytest.raises(PlatformError, match="Snowflake"):
        platform.run_agent("operations", THREAD, "0", [])
