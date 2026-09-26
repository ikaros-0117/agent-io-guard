from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.chat_clients.loader import (  # noqa: E402
    CLIENT_IDS,
    EXPECTED_ACTIONS,
    FIXTURE_ROOT,
    PROFILE_IDS,
    SCENARIOS,
    REQUIRED_FIELDS,
    FixtureValidationError,
    load_fixture,
    load_fixtures,
    validate_fixture,
)

SYNTHETIC_SECRET = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
SCENARIO_ACTIONS = {
    "normal": "NONE",
    "current_injection": "BLOCKED",
    "historical_injection_current_normal": "GUARDRAIL_INTERVENED",
    "system_runtime_context": "NONE",
    "tool_call": "BLOCKED",
    "tool_result": "BLOCKED",
    "input_secret": "GUARDRAIL_INTERVENED",
    "stream_secret": "GUARDRAIL_INTERVENED",
}


def _fixture_paths() -> list[Path]:
    return sorted(FIXTURE_ROOT.glob("*/*.json"))


@pytest.mark.parametrize(
    "path",
    _fixture_paths(),
    ids=lambda path: f"{path.parent.name}/{path.stem}",
)
def test_each_fixture_matches_shared_contract(path: Path) -> None:
    fixture = load_fixture(path, expected_client_id=path.parent.name)

    assert fixture.fixture_id == f"{fixture.client_id}.{fixture.scenario}"
    assert fixture.client_id in CLIENT_IDS
    assert fixture.profile_expected in PROFILE_IDS
    assert fixture.expected_action in EXPECTED_ACTIONS
    assert fixture.notes
    assert fixture.sanitized is True
    assert fixture.request["input_type"] in {"request", "response"}


def test_each_client_covers_the_required_m0_scenarios() -> None:
    fixtures = load_fixtures()
    coverage: dict[str, set[str]] = {}

    for fixture in fixtures:
        coverage.setdefault(fixture.client_id, set()).add(fixture.scenario)

    assert set(coverage) == CLIENT_IDS
    for client_id, scenarios in coverage.items():
        assert scenarios == SCENARIOS, client_id


def test_expected_actions_follow_the_scenario_contract() -> None:
    for fixture in load_fixtures():
        assert fixture.expected_action == SCENARIO_ACTIONS[fixture.scenario]


def test_input_fixtures_keep_the_litellm_guard_projection_shape() -> None:
    for fixture in load_fixtures():
        request = fixture.request
        assert set(
            {
                "input_type",
                "texts",
                "structured_messages",
                "tool_calls",
                "request_headers",
            }
        ).issubset(request)

        if fixture.stream:
            assert request["input_type"] == "response"
            assert request["structured_messages"] is None
            assert request["tool_calls"] is None
            assert request["request_headers"] is None
        else:
            assert request["input_type"] == "request"
            assert isinstance(request["structured_messages"], list)
            assert request["structured_messages"]
            assert isinstance(request["request_headers"], dict)


def test_blocked_fixtures_have_no_expected_upstream_messages() -> None:
    blocked = [fixture for fixture in load_fixtures() if fixture.expected_action == "BLOCKED"]

    assert blocked
    assert all(not fixture.expected_upstream_messages for fixture in blocked)


def test_normal_fixtures_preserve_input_messages() -> None:
    normal = [
        fixture
        for fixture in load_fixtures()
        if fixture.scenario in {"normal", "system_runtime_context"}
    ]

    assert normal
    for fixture in normal:
        assert list(fixture.expected_upstream_messages) == fixture.request["structured_messages"]


def test_historical_injection_is_replaced_and_current_turn_is_preserved() -> None:
    historical = [
        fixture
        for fixture in load_fixtures()
        if fixture.scenario == "historical_injection_current_normal"
    ]

    assert historical
    for fixture in historical:
        upstream = fixture.expected_upstream_messages
        assert upstream[0]["content"] == "[REMOVED_BY_AGENT_GUARD]"
        assert upstream[-1]["content"] == "Summarize the fixture status."
        assert fixture.request["structured_messages"][-1]["content"] == upstream[-1]["content"]


def test_secret_fixtures_do_not_expect_plaintext_upstream_or_output() -> None:
    secret_fixtures = [
        fixture for fixture in load_fixtures() if fixture.scenario in {"input_secret", "stream_secret"}
    ]

    assert secret_fixtures
    for fixture in secret_fixtures:
        upstream = json.dumps(fixture.expected_upstream_messages, ensure_ascii=False)
        response_texts = json.dumps(fixture.expected_response_texts, ensure_ascii=False)
        assert SYNTHETIC_SECRET not in upstream
        assert SYNTHETIC_SECRET not in response_texts
        assert "[REDACTED_SECRET]" in upstream or "[REDACTED_SECRET]" in response_texts


def test_tool_result_fixture_preserves_tool_chain_identity() -> None:
    tool_results = [fixture for fixture in load_fixtures() if fixture.scenario == "tool_result"]

    assert tool_results
    for fixture in tool_results:
        messages = fixture.request["structured_messages"]
        calls = fixture.request["tool_calls"]
        assistant = next(message for message in messages if message["role"] == "assistant")
        tool_result = next(message for message in messages if message["role"] == "tool")
        assert assistant["tool_calls"][0]["id"] == calls[0]["id"]
        assert tool_result["tool_call_id"] == calls[0]["id"]


def test_dsh_runtime_context_fixture_uses_folded_role_user_shape() -> None:
    fixture = load_fixture(FIXTURE_ROOT / "dsh_chat" / "system_runtime_context.json")

    messages = fixture.request["structured_messages"]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"].startswith("Current runtime context.")
    assert fixture.profile_expected == "dsh_chat"


def test_fixture_headers_are_sanitized() -> None:
    for fixture in load_fixtures():
        headers = fixture.request["request_headers"]
        if headers is None:
            continue
        assert headers["authorization"] == "[present]"
        assert headers["x-client-name"] == "[present]"


def test_schema_json_matches_loader_contract() -> None:
    schema = json.loads((FIXTURE_ROOT / "schema.json").read_text(encoding="utf-8"))

    assert set(schema["required"]) == set(REQUIRED_FIELDS)
    assert set(schema["properties"]["client_id"]["enum"]) == CLIENT_IDS
    assert set(schema["properties"]["profile_expected"]["enum"]) == PROFILE_IDS
    assert set(schema["properties"]["scenario"]["enum"]) == SCENARIOS
    assert set(schema["properties"]["expected_action"]["enum"]) == EXPECTED_ACTIONS


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_loader_rejects_missing_required_field(field: str) -> None:
    path = FIXTURE_ROOT / "generic_chat" / "normal.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document.pop(field)

    with pytest.raises(FixtureValidationError, match="missing fields"):
        validate_fixture(document, path=path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("client_id", "unknown_chat"),
        ("profile_expected", "unknown_chat"),
        ("expected_action", "ALLOW"),
    ],
)
def test_loader_rejects_invalid_identifiers_and_actions(field: str, value: str) -> None:
    path = FIXTURE_ROOT / "generic_chat" / "normal.json"
    document = copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))
    document[field] = value

    with pytest.raises(FixtureValidationError):
        validate_fixture(document, path=path)


def test_loader_rejects_non_sanitized_fixture() -> None:
    path = FIXTURE_ROOT / "generic_chat" / "normal.json"
    document = copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))
    document["sanitized"] = False

    with pytest.raises(FixtureValidationError, match="sanitized"):
        validate_fixture(document, path=path)
