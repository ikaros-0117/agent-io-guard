from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.chat_clients.loader import (  # noqa: E402
    CLIENT_IDS,
    CAPABILITY_IDS,
    SCENARIOS,
    ChatClientFixture,
    load_fixtures,
)

REQUEST_FIELDS = (
    "input_type",
    "texts",
    "structured_messages",
    "tool_calls",
    "request_headers",
    "model",
    "litellm_version",
    "additional_provider_specific_params",
)

# These are the only resolver-visible request differences found by the M0.1
# audit. An empty tuple means the generic and DSH fixtures have the same
# request projection and therefore cannot justify client-specific recognition.
EXPECTED_REQUEST_DIFFERENCES = {
    "normal": (),
    "current_injection": (),
    "historical_injection_current_normal": (),
    "system_runtime_context": ("texts", "structured_messages"),
    "tool_call": (),
    "tool_result": (),
    "input_secret": (),
    "stream_secret": (),
}

COMMON_ROLE_SEQUENCES = {
    "normal": ("system", "user"),
    "current_injection": ("user",),
    "historical_injection_current_normal": ("user", "assistant", "user"),
    "tool_call": ("assistant", "user"),
    "tool_result": ("assistant", "tool", "user"),
    "input_secret": ("user",),
    "stream_secret": (),
}

FALLBACK_REQUIRED_SCENARIOS = frozenset(
    {
        "normal",
        "current_injection",
        "historical_injection_current_normal",
        "tool_call",
        "tool_result",
        "input_secret",
    }
)

SANITIZED_REQUEST_HEADERS = {
    "accept": "application/json",
    "authorization": "[present]",
    "content-type": "application/json",
    "user-agent": "sanitized-chat-client/1.0",
    "x-client-name": "[present]",
}


@pytest.fixture(scope="module")
def fixtures() -> tuple[ChatClientFixture, ...]:
    return load_fixtures()


def _by_client(
    fixtures: tuple[ChatClientFixture, ...],
    scenario: str,
) -> dict[str, ChatClientFixture]:
    selected = {fixture.client_id: fixture for fixture in fixtures if fixture.scenario == scenario}
    assert set(selected) == CLIENT_IDS
    return selected


def _request_differences(
    generic: ChatClientFixture,
    dsh: ChatClientFixture,
) -> tuple[str, ...]:
    return tuple(
        field
        for field in REQUEST_FIELDS
        if generic.request[field] != dsh.request[field]
    )


def _role_sequence(fixture: ChatClientFixture) -> tuple[Any, ...]:
    messages = fixture.request["structured_messages"]
    if messages is None:
        return ()
    return tuple(message["role"] for message in messages)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_request_differences_match_the_m0_1_audit(
    fixtures: tuple[ChatClientFixture, ...],
    scenario: str,
) -> None:
    clients = _by_client(fixtures, scenario)

    assert _request_differences(clients["generic_chat"], clients["dsh_chat"]) == (
        EXPECTED_REQUEST_DIFFERENCES[scenario]
    )


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_role_sequence_differences_match_the_m0_1_audit(
    fixtures: tuple[ChatClientFixture, ...],
    scenario: str,
) -> None:
    clients = _by_client(fixtures, scenario)

    if scenario == "system_runtime_context":
        assert _role_sequence(clients["generic_chat"]) == ("system", "user")
        assert _role_sequence(clients["dsh_chat"]) == ("user", "user")
        return

    expected = COMMON_ROLE_SEQUENCES[scenario]
    assert _role_sequence(clients["generic_chat"]) == expected
    assert _role_sequence(clients["dsh_chat"]) == expected


@pytest.mark.parametrize("scenario", sorted(FALLBACK_REQUIRED_SCENARIOS))
def test_unidentifiable_dsh_requests_require_generic_fallback(
    fixtures: tuple[ChatClientFixture, ...],
    scenario: str,
) -> None:
    dsh = _by_client(fixtures, scenario)["dsh_chat"]

    assert dsh.profile_expected == "generic_chat"
    assert dsh.match_expectation == "fallback_required"
    assert dsh.expected_capabilities == ()


def test_system_runtime_context_declares_only_the_folded_context_capability(
    fixtures: tuple[ChatClientFixture, ...],
) -> None:
    clients = _by_client(fixtures, "system_runtime_context")
    generic = clients["generic_chat"]
    dsh = clients["dsh_chat"]

    assert generic.profile_expected == "generic_chat"
    assert generic.match_expectation == "expected"
    assert generic.expected_capabilities == ()
    assert dsh.profile_expected == "generic_chat"
    assert dsh.match_expectation == "capability_required"
    assert dsh.expected_capabilities == ("folded_runtime_context",)


def test_only_the_audited_fixture_declares_a_capability(
    fixtures: tuple[ChatClientFixture, ...],
) -> None:
    declared = {
        (fixture.client_id, fixture.scenario): fixture.expected_capabilities
        for fixture in fixtures
        if fixture.expected_capabilities
    }

    assert set(declared) == {("dsh_chat", "system_runtime_context")}
    assert set(declared[("dsh_chat", "system_runtime_context")]) <= CAPABILITY_IDS


def test_stream_fixtures_inherit_profile_from_the_request_phase(
    fixtures: tuple[ChatClientFixture, ...],
) -> None:
    stream_fixtures = [fixture for fixture in fixtures if fixture.stream]

    assert {fixture.client_id for fixture in stream_fixtures} == CLIENT_IDS
    for fixture in stream_fixtures:
        assert fixture.profile_expected == "generic_chat"
        assert fixture.match_expectation == "inherited_from_request"
        assert fixture.expected_capabilities == ()
        assert fixture.request["input_type"] == "response"
        assert fixture.request["structured_messages"] is None
        assert fixture.request["tool_calls"] is None
        assert fixture.request["request_headers"] is None


@pytest.mark.parametrize(
    "scenario",
    sorted(SCENARIOS - {"stream_secret"}),
)
def test_audited_input_fixtures_do_not_fabricate_client_headers(
    fixtures: tuple[ChatClientFixture, ...],
    scenario: str,
) -> None:
    clients = _by_client(fixtures, scenario)

    assert clients["generic_chat"].request["request_headers"] == SANITIZED_REQUEST_HEADERS
    assert clients["dsh_chat"].request["request_headers"] == SANITIZED_REQUEST_HEADERS


def test_non_runtime_metadata_is_identical_across_clients(
    fixtures: tuple[ChatClientFixture, ...],
) -> None:
    for scenario in SCENARIOS - {"system_runtime_context"}:
        clients = _by_client(fixtures, scenario)
        generic = clients["generic_chat"].request
        dsh = clients["dsh_chat"].request

        assert generic["model"] == dsh["model"]
        assert generic["litellm_version"] == dsh["litellm_version"]
        assert (
            generic["additional_provider_specific_params"]
            == dsh["additional_provider_specific_params"]
        )
