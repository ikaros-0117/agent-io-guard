"""Load and validate sanitized Chat client fixture documents."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "chat-client-fixture/v1"
FIXTURE_ROOT = Path(__file__).resolve().parent

CLIENT_IDS = frozenset({"generic_chat", "dsh_chat"})
PROFILE_IDS = frozenset({"generic_chat", "dsh_chat"})
EXPECTED_ACTIONS = frozenset({"NONE", "BLOCKED", "GUARDRAIL_INTERVENED"})
MATCH_EXPECTATIONS = frozenset(
    {
        "expected",
        "fallback_required",
        "capability_required",
        "inherited_from_request",
    }
)
CAPABILITY_IDS = frozenset({"folded_runtime_context", "tool_chain"})
SCENARIOS = frozenset(
    {
        "normal",
        "current_injection",
        "historical_injection_current_normal",
        "system_runtime_context",
        "tool_call",
        "tool_result",
        "input_secret",
        "stream_secret",
    }
)
REQUIRED_FIELDS = (
    "schema_version",
    "fixture_id",
    "client_id",
    "profile_expected",
    "match_expectation",
    "expected_capabilities",
    "scenario",
    "request",
    "expected_action",
    "expected_upstream_messages",
    "notes",
    "sanitized",
    "stream",
)
FIXTURE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
MESSAGE_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})


class FixtureValidationError(ValueError):
    """Raised when a Chat client fixture violates the shared schema."""


@dataclass(frozen=True, slots=True)
class ChatClientFixture:
    fixture_id: str
    client_id: str
    profile_expected: str
    match_expectation: str
    expected_capabilities: tuple[str, ...]
    scenario: str
    request: dict[str, Any]
    expected_action: str
    expected_upstream_messages: tuple[dict[str, Any], ...]
    expected_response_texts: tuple[str, ...] | None
    notes: str
    source_path: Path
    stream: bool
    sanitized: bool


def _fail(path: Path, message: str) -> None:
    raise FixtureValidationError(f"{path}: {message}")


def _require_mapping(path: Path, value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, f"{field} must be an object")
    return value


def _require_string(path: Path, value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(path, f"{field} must be a non-empty string")
    return value


def _require_string_list(path: Path, value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _fail(path, f"{field} must be an array of strings")
    return value


def _content_texts(path: Path, content: Any, field: str) -> list[str]:
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, dict):
        text = content.get("text")
        if not isinstance(text, str):
            _fail(path, f"{field} object content must contain a string text")
        return [text]
    if isinstance(content, list):
        result: list[str] = []
        for block_index, block in enumerate(content):
            if isinstance(block, str):
                result.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                result.append(block["text"])
            else:
                _fail(path, f"{field}[{block_index}] must be text-shaped")
        return result
    _fail(path, f"{field} must be a string, text block, or null")


def _validate_headers(path: Path, value: Any, field: str, *, allow_null: bool) -> None:
    if value is None and allow_null:
        return
    headers = _require_mapping(path, value, field)
    for key, header_value in headers.items():
        if not isinstance(key, str) or not isinstance(header_value, str):
            _fail(path, f"{field} keys and values must be strings")


def _validate_request(path: Path, request: dict[str, Any], *, stream: bool, scenario: str) -> None:
    required = (
        "input_type",
        "texts",
        "structured_messages",
        "tool_calls",
        "request_headers",
        "model",
        "litellm_version",
        "additional_provider_specific_params",
    )
    missing = [field for field in required if field not in request]
    if missing:
        _fail(path, f"request missing fields: {', '.join(missing)}")

    input_type = request["input_type"]
    if input_type not in {"request", "response"}:
        _fail(path, "request.input_type must be request or response")
    texts = _require_string_list(path, request["texts"], "request.texts")
    tool_calls = request["tool_calls"]
    if tool_calls is not None and (
        not isinstance(tool_calls, list) or any(not isinstance(call, dict) for call in tool_calls)
    ):
        _fail(path, "request.tool_calls must be an array of objects or null")
    _require_string(path, request["model"], "request.model")
    _require_string(path, request["litellm_version"], "request.litellm_version")
    _require_mapping(
        path,
        request["additional_provider_specific_params"],
        "request.additional_provider_specific_params",
    )

    structured_messages = request["structured_messages"]
    if input_type == "response":
        if not stream or scenario != "stream_secret":
            _fail(path, "response fixtures are only supported for stream_secret")
        if structured_messages is not None or tool_calls is not None:
            _fail(path, "stream response fixtures must not contain structured messages or tool calls")
        if request["request_headers"] is not None:
            _fail(path, "stream response fixtures must not contain request_headers")
        if not texts:
            _fail(path, "stream response fixtures must contain cumulative texts")
        return

    if stream or scenario == "stream_secret":
        _fail(path, "stream_secret must use a stream response request")
    if not isinstance(structured_messages, list) or not structured_messages:
        _fail(path, "input fixtures must contain structured_messages")
    _validate_headers(path, request["request_headers"], "request.request_headers", allow_null=False)

    flattened: list[str] = []
    for message_index, message in enumerate(structured_messages):
        message = _require_mapping(path, message, f"request.structured_messages[{message_index}]")
        role = _require_string(path, message.get("role"), f"request.structured_messages[{message_index}].role")
        if role not in MESSAGE_ROLES:
            _fail(path, f"unsupported structured message role: {role}")
        flattened.extend(
            _content_texts(
                path,
                message.get("content"),
                f"request.structured_messages[{message_index}].content",
            )
        )
    if flattened != texts:
        _fail(path, "request.texts must match structured message text order")


def _validate_upstream_message(path: Path, value: Any, index: int) -> None:
    message = _require_mapping(path, value, f"expected_upstream_messages[{index}]")
    role = _require_string(path, message.get("role"), f"expected_upstream_messages[{index}].role")
    if role not in MESSAGE_ROLES:
        _fail(path, f"unsupported expected upstream role: {role}")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        _fail(path, f"expected_upstream_messages[{index}].content must be a string or null")


def validate_fixture(
    document: Any,
    *,
    path: Path,
    expected_client_id: str | None = None,
) -> ChatClientFixture:
    """Validate one decoded fixture document and return its typed projection."""
    fixture = _require_mapping(path, document, "fixture")
    missing = [field for field in REQUIRED_FIELDS if field not in fixture]
    if missing:
        _fail(path, f"missing fields: {', '.join(missing)}")

    if fixture["schema_version"] != SCHEMA_VERSION:
        _fail(path, f"schema_version must be {SCHEMA_VERSION}")
    fixture_id = _require_string(path, fixture["fixture_id"], "fixture_id")
    if not FIXTURE_ID_PATTERN.fullmatch(fixture_id):
        _fail(path, "fixture_id must use '<client_id>.<scenario>' naming")
    client_id = _require_string(path, fixture["client_id"], "client_id")
    if client_id not in CLIENT_IDS:
        _fail(path, f"unsupported client_id: {client_id}")
    if expected_client_id is not None and client_id != expected_client_id:
        _fail(path, f"client_id must match directory {expected_client_id}")
    profile_expected = _require_string(path, fixture["profile_expected"], "profile_expected")
    if profile_expected not in PROFILE_IDS:
        _fail(path, f"unsupported profile_expected: {profile_expected}")
    match_expectation = _require_string(path, fixture["match_expectation"], "match_expectation")
    if match_expectation not in MATCH_EXPECTATIONS:
        _fail(path, f"unsupported match_expectation: {match_expectation}")
    expected_capabilities = tuple(
        _require_string_list(
            path,
            fixture["expected_capabilities"],
            "expected_capabilities",
        )
    )
    unsupported_capabilities = set(expected_capabilities) - CAPABILITY_IDS
    if unsupported_capabilities:
        _fail(
            path,
            "unsupported expected_capabilities: "
            + ", ".join(sorted(unsupported_capabilities)),
        )
    if len(expected_capabilities) != len(set(expected_capabilities)):
        _fail(path, "expected_capabilities must not contain duplicates")
    scenario = _require_string(path, fixture["scenario"], "scenario")
    if scenario not in SCENARIOS:
        _fail(path, f"unsupported scenario: {scenario}")
    if fixture_id != f"{client_id}.{scenario}":
        _fail(path, "fixture_id must equal '<client_id>.<scenario>'")
    if path.stem != scenario:
        _fail(path, f"filename must match scenario: {scenario}.json")

    request = _require_mapping(path, fixture["request"], "request")
    expected_action = _require_string(path, fixture["expected_action"], "expected_action")
    if expected_action not in EXPECTED_ACTIONS:
        _fail(path, f"unsupported expected_action: {expected_action}")
    expected_messages = fixture["expected_upstream_messages"]
    if not isinstance(expected_messages, list):
        _fail(path, "expected_upstream_messages must be an array")
    for index, message in enumerate(expected_messages):
        _validate_upstream_message(path, message, index)

    notes = _require_string(path, fixture["notes"], "notes")
    if fixture["sanitized"] is not True:
        _fail(path, "sanitized must be true")
    stream = fixture["stream"]
    if not isinstance(stream, bool):
        _fail(path, "stream must be a boolean")
    _validate_request(path, request, stream=stream, scenario=scenario)

    if match_expectation == "fallback_required" and profile_expected != "generic_chat":
        _fail(path, "fallback_required fixtures must expect generic_chat")
    if match_expectation == "capability_required":
        if profile_expected != "generic_chat":
            _fail(path, "capability_required fixtures must expect generic_chat")
        if not expected_capabilities:
            _fail(path, "capability_required fixtures must declare expected_capabilities")
    elif expected_capabilities:
        _fail(path, "expected_capabilities require match_expectation=capability_required")
    if match_expectation == "inherited_from_request" and not stream:
        _fail(path, "inherited_from_request is only valid for response fixtures")
    if match_expectation != "inherited_from_request" and stream:
        _fail(path, "stream fixtures must inherit profile from the request phase")

    expected_response_texts: tuple[str, ...] | None = None
    if "expected_response_texts" in fixture:
        expected_response_texts = tuple(
            _require_string_list(
                path,
                fixture["expected_response_texts"],
                "expected_response_texts",
            )
        )
    if scenario == "stream_secret" and not expected_response_texts:
        _fail(path, "stream_secret must define non-empty expected_response_texts")

    return ChatClientFixture(
        fixture_id=fixture_id,
        client_id=client_id,
        profile_expected=profile_expected,
        match_expectation=match_expectation,
        expected_capabilities=expected_capabilities,
        scenario=scenario,
        request=request,
        expected_action=expected_action,
        expected_upstream_messages=tuple(expected_messages),
        expected_response_texts=expected_response_texts,
        notes=notes,
        source_path=path,
        stream=stream,
        sanitized=True,
    )


def load_fixture(path: Path, *, expected_client_id: str | None = None) -> ChatClientFixture:
    """Load one fixture from a JSON file."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureValidationError(f"{path}: invalid JSON: {exc}") from exc
    return validate_fixture(document, path=path, expected_client_id=expected_client_id)


def load_fixtures(root: Path = FIXTURE_ROOT) -> tuple[ChatClientFixture, ...]:
    """Load every client fixture below the fixture root, sorted by path."""
    fixtures: list[ChatClientFixture] = []
    for path in sorted(root.glob("*/*.json")):
        expected_client_id = path.parent.name
        fixtures.append(load_fixture(path, expected_client_id=expected_client_id))
    fixture_ids = [fixture.fixture_id for fixture in fixtures]
    if len(fixture_ids) != len(set(fixture_ids)):
        raise FixtureValidationError("fixture_id values must be unique")
    return tuple(fixtures)
