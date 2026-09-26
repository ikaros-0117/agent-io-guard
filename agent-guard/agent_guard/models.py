from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .detector import Decision
from .rules import RuleAction


class GuardResponsePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    content: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class GuardCheckRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str = Field(default_factory=lambda: f"req-{uuid4()}")
    trace_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    phase: Literal["input", "output"]
    model: str | None = None
    policy_id: str = "default-agent-policy"
    texts: list[str] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    response: GuardResponsePayload | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LiteLLMGuardrailRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    input_type: Literal["request", "response"]
    litellm_call_id: str | None = None
    litellm_trace_id: str | None = None
    structured_messages: list[dict[str, Any]] | None = None
    images: list[str] | None = None
    tools: list[dict[str, Any]] | None = None
    texts: list[str] | None = None
    request_data: dict[str, Any] = Field(default_factory=dict)
    request_headers: dict[str, str] | None = None
    litellm_version: str | None = None
    additional_provider_specific_params: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    model: str | None = None


class FindingResponse(BaseModel):
    rule_id: str
    category: str
    action: RuleAction
    source: str
    source_type: str
    risk_level: str


class SanitizedItemResponse(BaseModel):
    source: str
    text: str


class CacheResponse(BaseModel):
    status: str = "bypassed"
    store: str | None = None


class DetectorVersionsResponse(BaseModel):
    normalizer: str = "1"
    rules: str


class GuardCheckResponse(BaseModel):
    decision: Decision
    risk_level: str
    categories: list[str]
    score: float
    sanitized: list[SanitizedItemResponse] | None = None
    reason_codes: list[str]
    layer_trace: list[str]
    policy_version: str
    detector_versions: DetectorVersionsResponse
    cache: CacheResponse = Field(default_factory=CacheResponse)
    latency_ms: dict[str, float]
    request_id: str
    trace_id: str | None = None
    findings: list[FindingResponse] = Field(default_factory=list)


class LiteLLMGuardrailResponse(BaseModel):
    action: Literal["NONE", "BLOCKED", "GUARDRAIL_INTERVENED"]
    blocked_reason: str | None = None
    texts: list[str] | None = None
    stream_holdback_chars: list[int] | None = Field(default=None, exclude_if=lambda value: value is None)
