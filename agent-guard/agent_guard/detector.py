from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum

from .config import Settings
from .normalization import match_view, redaction_view
from .rules import Rule, RuleAction, STATIC_RULES


class Decision(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    REDACT = "redact"
    SUSPICIOUS = "suspicious"


class InputTooLarge(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TextItem:
    source: str
    text: str
    phase: str
    source_type: str
    rewritable: bool = True


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    category: str
    action: RuleAction
    source: str
    source_type: str
    risk_level: str


@dataclass(frozen=True, slots=True)
class SanitizedItem:
    source: str
    text: str


@dataclass(frozen=True, slots=True)
class DetectionResult:
    decision: Decision
    risk_level: str
    categories: tuple[str, ...]
    findings: tuple[Finding, ...]
    sanitized: tuple[SanitizedItem, ...]
    reason_codes: tuple[str, ...]
    layer_trace: tuple[str, ...]
    latency_ms: float


class StaticRuleDetector:
    def __init__(
        self,
        settings: Settings,
        rules: tuple[Rule, ...] = STATIC_RULES,
    ) -> None:
        self.settings = settings
        self.rules = rules

    def check(self, items: list[TextItem]) -> DetectionResult:
        self._validate_limits(items)
        started = time.perf_counter()
        findings: list[Finding] = []
        sanitized: list[SanitizedItem] = []
        unredactable_redaction = False

        for item in items:
            folded = match_view(item.text)
            redactable = redaction_view(item.text)
            item_redacted = False

            for rule in self.rules:
                if not rule.applies_to(phase=item.phase, source_type=item.source_type):
                    continue
                if rule.first_match(folded) is None:
                    continue

                findings.append(
                    Finding(
                        rule_id=rule.rule_id,
                        category=rule.category,
                        action=rule.action,
                        source=item.source,
                        source_type=item.source_type,
                        risk_level=rule.risk_level,
                    )
                )

                if rule.action == RuleAction.REDACT:
                    for pattern in rule.patterns:
                        redactable = pattern.sub_valid(rule.replacement, redactable)
                    item_redacted = True

            if item_redacted:
                if item.rewritable:
                    sanitized.append(SanitizedItem(source=item.source, text=redactable))
                else:
                    unredactable_redaction = True

        decision, risk_level = self._decide(findings, bool(sanitized), unredactable_redaction)
        latency_ms = (time.perf_counter() - started) * 1000
        return DetectionResult(
            decision=decision,
            risk_level=risk_level,
            categories=tuple(dict.fromkeys(finding.category for finding in findings)),
            findings=tuple(findings),
            sanitized=tuple(sanitized),
            reason_codes=tuple(dict.fromkeys(finding.rule_id for finding in findings)),
            layer_trace=self._layer_trace(decision, findings),
            latency_ms=latency_ms,
        )

    def _validate_limits(self, items: list[TextItem]) -> None:
        if len(items) > self.settings.max_texts:
            raise InputTooLarge(
                f"too many text values: {len(items)} > {self.settings.max_texts}"
            )

        total_chars = 0
        for item in items:
            text_length = len(item.text)
            if text_length > self.settings.max_text_chars:
                raise InputTooLarge(
                    f"text value exceeds {self.settings.max_text_chars} characters"
                )
            total_chars += text_length
        if total_chars > self.settings.max_total_chars:
            raise InputTooLarge(
                f"total text exceeds {self.settings.max_total_chars} characters"
            )

    @staticmethod
    def _decide(
        findings: list[Finding],
        redacted: bool,
        unredactable_redaction: bool,
    ) -> tuple[Decision, str]:
        if any(finding.action == RuleAction.HARD_BLOCK for finding in findings):
            risks = {finding.risk_level for finding in findings}
            return Decision.BLOCK, "critical" if "critical" in risks else "high"
        if unredactable_redaction:
            return Decision.BLOCK, "high"
        if redacted:
            return Decision.REDACT, "medium"
        if any(finding.action == RuleAction.SUSPICIOUS for finding in findings):
            return Decision.SUSPICIOUS, "low"
        return Decision.ALLOW, "safe"

    @staticmethod
    def _layer_trace(decision: Decision, findings: list[Finding]) -> tuple[str, ...]:
        if not findings:
            return ("l1_miss", f"decision_{decision.value}")
        return (f"l1_hit:{len(findings)}", f"decision_{decision.value}")
