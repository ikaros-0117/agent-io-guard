from __future__ import annotations

import hmac
import json
import logging
import re
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request

from .config import Settings
from .detector import (
    DetectionResult,
    InputTooLarge,
    StaticRuleDetector,
    TextItem,
)
from .models import (
    GuardCheckRequest,
    GuardCheckResponse,
    LiteLLMGuardrailRequest,
    LiteLLMGuardrailResponse,
)
from .rules import STATIC_RULES, RuleAction

logger = logging.getLogger("agent_guard")

STATIC_RULES_VERSION = "2026-09-21.2"
HISTORICAL_BLOCK_PLACEHOLDER = "[REMOVED_BY_AGENT_GUARD]"
_TEXT_SOURCE_RE = re.compile(r"^texts\[(\d+)\]$")
_SYNTHETIC_USER_PREFIXES = (
    "Current runtime context.",
    "<system-reminder>",
    "<runtime-context>",
    "<environment_context>",
)


def _content_texts(content: Any) -> list[str]:
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        values: list[str] = []
        for part in content:
            if isinstance(part, str):
                values.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                values.append(part["text"])
        return values
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return [content["text"]]
    if isinstance(content, (int, float, bool)):
        return [str(content)]
    return []


def _tool_call_items(
    tool_calls: list[dict[str, Any]] | None,
    *,
    phase: str,
    source_prefix: str,
    rewritable: bool,
) -> list[TextItem]:
    items: list[TextItem] = []
    for index, tool_call in enumerate(tool_calls or []):
        function = tool_call.get("function") if isinstance(tool_call, dict) else None
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if arguments is None:
            arguments = tool_call
        if isinstance(arguments, str):
            values = [arguments] if arguments else []
        else:
            values = [json.dumps(arguments, ensure_ascii=False, sort_keys=True)]
        for value in values:
            items.append(
                TextItem(
                    source=f"{source_prefix}[{index}].function.arguments",
                    text=value,
                    phase=phase,
                    source_type="tool_call",
                    rewritable=rewritable,
                )
            )
    return items


def _message_items(
    messages: list[dict[str, Any]],
    *,
    phase: str,
    rewritable: bool,
) -> list[TextItem]:
    items: list[TextItem] = []
    for index, message in enumerate(messages):
        for value in _content_texts(message.get("content")):
            items.append(
                TextItem(
                    source=f"messages[{index}].content",
                    text=value,
                    phase=phase,
                    source_type="message",
                    rewritable=rewritable,
                )
            )
        items.extend(
            _tool_call_items(
                message.get("tool_calls"),
                phase=phase,
                source_prefix=f"messages[{index}].tool_calls",
                rewritable=rewritable,
            )
        )
        legacy_call = message.get("function_call")
        if isinstance(legacy_call, dict):
            items.extend(
                _tool_call_items(
                    [{"function": legacy_call}],
                    phase=phase,
                    source_prefix=f"messages[{index}].function_call",
                    rewritable=rewritable,
                )
            )
    return items


def _deduplicate(items: list[TextItem]) -> list[TextItem]:
    seen: set[tuple[str, str, str]] = set()
    result: list[TextItem] = []
    for item in items:
        key = (item.phase, item.source_type, item.text)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _is_synthetic_user_text(value: str) -> bool:
    stripped = value.lstrip()
    return any(stripped.startswith(prefix) for prefix in _SYNTHETIC_USER_PREFIXES)


def _latest_user_text_indices(messages: list[dict[str, Any]]) -> set[int] | None:
    """Map the latest real user turn into LiteLLM's flat texts list.

    Harness/pi-ai folds in-history system messages into ``role=user`` messages.
    Runtime-context snapshots therefore appear after the real user input; they
    must not displace the actual human turn in latest-turn policy decisions.
    """
    text_index = 0
    latest_user_indices: set[int] | None = None
    for message in messages:
        values = _content_texts(message.get("content"))
        if str(message.get("role") or "").lower() == "user" and values:
            current_indices = {
                text_index + offset
                for offset, value in enumerate(values)
                if not _is_synthetic_user_text(value)
            }
            if current_indices:
                latest_user_indices = current_indices
        text_index += len(values)
    return latest_user_indices


def _text_source_index(source: str) -> int | None:
    match = _TEXT_SOURCE_RE.fullmatch(source)
    return int(match.group(1)) if match else None


def _apply_text_replacements(
    texts: list[str],
    replacements: dict[str, str],
) -> bool:
    changed = False
    for source, replacement in replacements.items():
        index = _text_source_index(source)
        if index is None or index >= len(texts) or texts[index] == replacement:
            continue
        texts[index] = replacement
        changed = True
    return changed


def _litellm_response(
    payload: LiteLLMGuardrailRequest,
    result: DetectionResult,
    request_id: str,
) -> LiteLLMGuardrailResponse:
    texts = list(payload.texts or [])
    sanitized_sources = {item.source for item in result.sanitized}
    latest_user_indices = (
        _latest_user_text_indices(payload.structured_messages or [])
        if payload.input_type == "request"
        else None
    )

    historical_hard_indices: set[int] = set()
    current_hard_block = False
    unresolved_redaction = False

    for finding in result.findings:
        source_index = _text_source_index(finding.source)
        if finding.action == RuleAction.HARD_BLOCK:
            if (
                source_index is not None
                and latest_user_indices is not None
                and source_index not in latest_user_indices
            ):
                historical_hard_indices.add(source_index)
            else:
                current_hard_block = True
        elif finding.action == RuleAction.REDACT and finding.source not in sanitized_sources:
            unresolved_redaction = True

    if current_hard_block or unresolved_redaction:
        return LiteLLMGuardrailResponse(
            action="BLOCKED",
            blocked_reason=_block_message(result, request_id),
        )

    if historical_hard_indices and result.decision.value == "block":
        if any(index >= len(texts) for index in historical_hard_indices):
            return LiteLLMGuardrailResponse(
                action="BLOCKED",
                blocked_reason=_block_message(result, request_id),
            )
        replacements = {item.source: item.text for item in result.sanitized}
        _apply_text_replacements(texts, replacements)
        for index in historical_hard_indices:
            texts[index] = HISTORICAL_BLOCK_PLACEHOLDER
        return LiteLLMGuardrailResponse(
            action="GUARDRAIL_INTERVENED",
            texts=texts,
        )

    if result.decision.value == "redact":
        replacements = {item.source: item.text for item in result.sanitized}
        if _apply_text_replacements(texts, replacements):
            return LiteLLMGuardrailResponse(
                action="GUARDRAIL_INTERVENED",
                texts=texts,
            )
        return LiteLLMGuardrailResponse(
            action="BLOCKED",
            blocked_reason=_block_message(result, request_id),
        )

    return LiteLLMGuardrailResponse(action="NONE")


def _canonical_items(payload: GuardCheckRequest) -> list[TextItem]:
    phase = payload.phase
    source_type = "response" if phase == "output" else "message"
    items = [
        TextItem(
            source=f"texts[{index}]",
            text=text,
            phase=phase,
            source_type=source_type,
            rewritable=True,
        )
        for index, text in enumerate(payload.texts)
        if text
    ]
    items.extend(_message_items(payload.messages, phase=phase, rewritable=True))
    if payload.response is not None:
        for index, value in enumerate(_content_texts(payload.response.content)):
            items.append(
                TextItem(
                    source=f"response.content[{index}]",
                    text=value,
                    phase="output",
                    source_type="response",
                    rewritable=True,
                )
            )
        items.extend(
            _tool_call_items(
                payload.response.tool_calls,
                phase="output",
                source_prefix="response.tool_calls",
                rewritable=True,
            )
        )
    return _deduplicate(items)


def _litellm_items(payload: LiteLLMGuardrailRequest) -> list[TextItem]:
    phase = "output" if payload.input_type == "response" else "input"
    source_type = "response" if phase == "output" else "message"
    items: list[TextItem] = []

    if payload.texts is not None:
        items.extend(
            TextItem(
                source=f"texts[{index}]",
                text=text,
                phase=phase,
                source_type=source_type,
                rewritable=True,
            )
            for index, text in enumerate(payload.texts)
        )
    elif payload.structured_messages:
        items.extend(
            _message_items(
                payload.structured_messages,
                phase=phase,
                rewritable=False,
            )
        )

    items.extend(
        _tool_call_items(
            payload.tool_calls,
            phase=phase,
            source_prefix="tool_calls",
            rewritable=False,
        )
    )
    return items


def _response_from_result(
    result: DetectionResult,
    *,
    request_id: str,
    trace_id: str | None,
    policy_version: str,
) -> GuardCheckResponse:
    score_by_decision = {
        "allow": 0.0,
        "suspicious": 0.3,
        "redact": 0.6,
        "block": 1.0,
    }
    return GuardCheckResponse(
        decision=result.decision,
        risk_level=result.risk_level,
        categories=list(result.categories),
        score=score_by_decision[result.decision.value],
        sanitized=(
            [
                {"source": item.source, "text": item.text}
                for item in result.sanitized
            ]
            if result.sanitized
            else None
        ),
        reason_codes=list(result.reason_codes),
        layer_trace=list(result.layer_trace),
        policy_version=policy_version,
        detector_versions={
            "normalizer": "1",
            "rules": STATIC_RULES_VERSION,
        },
        latency_ms={"total": result.latency_ms, "l1": result.latency_ms},
        request_id=request_id,
        trace_id=trace_id,
        findings=[
            {
                "rule_id": finding.rule_id,
                "category": finding.category,
                "action": finding.action,
                "source": finding.source,
                "source_type": finding.source_type,
                "risk_level": finding.risk_level,
            }
            for finding in result.findings
        ],
    )


def _authorize(request: Request, settings: Settings) -> None:
    candidates: list[str] = []
    api_key = request.headers.get("x-api-key")
    if api_key:
        candidates.append(api_key)
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        candidates.append(authorization[7:].strip())

    if not any(hmac.compare_digest(candidate, settings.token) for candidate in candidates):
        raise HTTPException(
            status_code=401,
            detail="invalid internal token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _block_message(result: DetectionResult, request_id: str) -> str:
    categories = ",".join(result.categories) or "static_policy"
    return f"Blocked by L1 static policy ({categories}); request_id={request_id}"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    detector = StaticRuleDetector(settings)
    app = FastAPI(
        title="agent-guard",
        version="0.1.0",
        description="L1 static input/output rule detection for LiteLLM.",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "policy_version": settings.policy_version,
            "rules_version": STATIC_RULES_VERSION,
            "rule_count": len(STATIC_RULES),
        }

    @app.post("/v1/guard/check", response_model=GuardCheckResponse)
    def guard_check(payload: GuardCheckRequest, request: Request) -> GuardCheckResponse:
        _authorize(request, settings)
        items = _canonical_items(payload)
        try:
            result = detector.check(items)
        except InputTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

        trace_id = payload.trace_id or request.headers.get("x-trace-id")
        logger.info(
            "guard check request_id=%s phase=%s decision=%s findings=%s latency_ms=%.3f",
            payload.request_id,
            payload.phase,
            result.decision.value,
            len(result.findings),
            result.latency_ms,
        )
        return _response_from_result(
            result,
            request_id=payload.request_id,
            trace_id=trace_id,
            policy_version=settings.policy_version,
        )

    @app.post(
        "/beta/litellm_basic_guardrail_api",
        response_model=LiteLLMGuardrailResponse,
    )
    def litellm_basic_guardrail(
        payload: LiteLLMGuardrailRequest,
        request: Request,
    ) -> LiteLLMGuardrailResponse:
        _authorize(request, settings)
        items = _litellm_items(payload)
        try:
            result = detector.check(items)
        except InputTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

        request_id = (
            request.headers.get("x-request-id")
            or payload.litellm_call_id
            or f"req-{uuid4()}"
        )
        response = _litellm_response(payload, result, request_id)
        logger.info(
            "litellm guardrail request_id=%s input_type=%s decision=%s action=%s findings=%s latency_ms=%.3f",
            request_id,
            payload.input_type,
            result.decision.value,
            response.action,
            len(result.findings),
            result.latency_ms,
        )
        return response

    return app
