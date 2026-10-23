"""Console navigation must never authenticate, run SQL, or disclose credentials."""

import json

import pytest
from fastapi.testclient import TestClient

import app.config as config
import app.main as api
from app.config import Settings
from app.snowflake_links import workspace_links


def settings(**updates):
    return Settings(
        _env_file=None,
        user="private-user",
        token="PRIVATE_TOKEN",
        password="PRIVATE_PASSWORD",
        account="my-org-account",
        database="TEST_DB",
        **updates,
    )


def test_workspace_endpoint_is_read_only_and_excludes_credentials(monkeypatch):
    monkeypatch.setattr(config, "get_settings", settings)

    def forbidden(*args, **kwargs):
        raise AssertionError("Console links must not query Snowflake")

    monkeypatch.setattr(api, "query", forbidden)
    response = TestClient(api.app).get("/api/snowflake/workspace")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "configured"
    assert data["resources"]["audit"]["url"] == (
        "https://app.snowflake.com/my/org-account/#/data/databases/TEST_DB/"
        "schemas/SUPPLY_RECOVERY_V1/table/AUDIT_RECORDS"
    )
    assert "PRIVATE_" not in response.text and "private-user" not in response.text
    assert "password" not in data and "token" not in data
    assert data["resources"]["outcomes"]["object"].endswith("SUPPLY_RECOVERY_V1_EVAL.RESULT_RECORDS")
    assert data["resources"]["search"]["url"].endswith("/cortex-search-service/EVIDENCE_SEARCH")
    assert data["resources"]["analyst"]["url"].endswith(
        "/semantic-view/RECOVERY_ANALYTICS/semantic-information"
    )
    assert data["agents"]["operations"]["url"].endswith(
        "/agents/database/TEST_DB/schema/SUPPLY_RECOVERY_V1/agent/SUMO_OPERATIONS_V1/details"
    )
    assert data["agents"]["qualification"]["executions_url"].endswith(
        "SUMO_QUALIFICATION_V1/details?tab=observability&preset=PRESET_LAST_DAY&type=relative"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/org/account",
        "https://app.snowflake.com.attacker.example/org/account",
        "https://user:secret@app.snowflake.com/org/account",
        "http://app.snowflake.com/org/account",
        "https://app.snowflake.com/org/account?token=secret",
        "https://app.snowflake.com/org/account/#/other",
        "javascript:alert(1)",
    ],
)
def test_console_url_cannot_redirect_or_carry_credentials(url):
    with pytest.raises(ValueError):
        workspace_links(settings(snowsight_url=url))


def test_locator_configuration_requires_explicit_console_url():
    selected = settings().model_copy(update={"account": "xy12345.us-east-1"})
    with pytest.raises(ValueError):
        workspace_links(selected)
    selected = selected.model_copy(update={"snowsight_url": "https://app.snowflake.com/us-east-1/xy12345/"})
    assert workspace_links(selected)["base_url"] == "https://app.snowflake.com/us-east-1/xy12345"


def test_missing_settings_leave_demo_usable_without_leaking_error(monkeypatch):
    def unavailable():
        raise ValueError("secret configuration details")

    monkeypatch.setattr(config, "get_settings", unavailable)
    response = TestClient(api.app).get("/api/snowflake/workspace")
    assert response.status_code == 200
    assert response.json()["status"] == "unconfigured"
    assert "secret configuration details" not in json.dumps(response.json())
