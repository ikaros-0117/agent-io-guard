"""Conservative incremental scanning of cumulative LiteLLM output texts.

A comma is a safe boundary for current output text rules except a PEM block.
Do not reuse a prefix when PEM/escape/unicode content could invalidate that
proof. All other cases fall back to the regular full scanner. Cached raw text
is bounded by output limits, LRU capacity and a short idle TTL.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from .detector import DetectionResult, StaticRuleDetector, TextItem
from .rules import STATIC_RULES

# Changing the output rule set requires an explicit review of the comma-cut
# proof. Until that happens, new/removed rules automatically use full scans.
SAFE_OUTPUT_RULE_IDS = frozenset({
    "secret.openai_api_key", "secret.github_token", "secret.aws_access_key",
    "secret.slack_token", "secret.jwt", "secret.authorization_header",
    "secret.private_key", "secret.assignment", "pii.email", "pii.cn_mobile",
    "pii.cn_id", "pii.bank_card",
})


@dataclass(frozen=True, slots=True)
class OutputScan:
    result: DetectionResult
    sanitized_texts: tuple[str, ...]
    incremental: bool


@dataclass(slots=True)
class _Entry:
    raw: tuple[str, ...]
    sanitized: tuple[str, ...]
    result: DetectionResult
    last_seen: float


class OutputStreamScanner:
    def __init__(self, detector: StaticRuleDetector, *, max_calls: int = 8, idle_ttl: int = 300) -> None:
        self.detector = detector
        self.max_calls = max_calls
        self.idle_ttl = idle_ttl
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = Lock()
        self._incremental_rules_safe = (
            detector.rules is STATIC_RULES
            and frozenset(rule.rule_id for rule in detector.rules
                           if rule.applies_to(phase="output", source_type="response"))
            == SAFE_OUTPUT_RULE_IDS
        )

    @staticmethod
    def _safe_cut(previous: str) -> int:
        # ASCII comma cannot occur inside any current output secret/PII rule,
        # except PEM's [\s\S]*. A PEM marker anywhere disables this shortcut.
        # URL/HTML escapes and Unicode normalization might turn raw delimiters
        # into matchable text, so those inputs must use the complete scanner.
        if not previous.isascii() or "&" in previous or "%" in previous:
            return 0
        if "-----begin" in previous.lower():
            return 0
        return previous.rfind(",") + 1

    @staticmethod
    def _items(texts: tuple[str, ...]) -> list[TextItem]:
        return [TextItem(source=f"texts[{index}]", text=text, phase="output", source_type="response")
                for index, text in enumerate(texts)]

    @staticmethod
    def _redacted(texts: tuple[str, ...], result: DetectionResult) -> tuple[str, ...]:
        replacements = {item.source: item.text for item in result.sanitized}
        return tuple(replacements.get(f"texts[{index}]", text) for index, text in enumerate(texts))

    def scan(self, call_id: str, texts: list[str]) -> OutputScan:
        raw = tuple(texts)
        # Validate the *full* cumulative payload even when only a suffix is
        # scanned, preserving the output policy cap and fail-closed behavior.
        self.detector.validate_limits(self._items(raw), output_limits=True)
        now = time.monotonic()
        with self._lock:
            for key in list(self._entries):
                if now - self._entries[key].last_seen >= self.idle_ttl:
                    del self._entries[key]
            prior = self._entries.get(call_id)
            if prior is not None and raw == prior.raw:
                prior.last_seen = now
                self._entries.move_to_end(call_id)
                return OutputScan(prior.result, prior.sanitized, incremental=True)

            cuts: list[int] = []
            if (prior is not None and len(raw) == len(prior.raw)
                and all(text.startswith(old) for text, old in zip(raw, prior.raw))):
                cuts = [self._safe_cut(old) for old in prior.raw]

            incremental = self._incremental_rules_safe and bool(cuts) and all(cut > 0 for cut in cuts)
            if incremental:
                suffixes = tuple(text[cut:] for text, cut in zip(raw, cuts))
                result = self.detector.check(self._items(suffixes), output_limits=True)
                scanned = self._redacted(suffixes, result)
                sanitized: list[str] = []
                for old_sanitized, cut, suffix in zip(prior.sanitized, cuts, scanned):
                    # Redaction placeholders contain no comma and no non-PEM
                    # match spans a comma, so the last comma survives verbatim.
                    boundary = old_sanitized.rfind(",") + 1
                    if boundary <= 0:
                        incremental = False
                        break
                    sanitized.append(old_sanitized[:boundary] + suffix)
                if incremental:
                    full_sanitized = tuple(sanitized)
            if not incremental:
                result = self.detector.check(self._items(raw), output_limits=True)
                full_sanitized = self._redacted(raw, result)

            self._entries[call_id] = _Entry(raw, full_sanitized, result, now)
            self._entries.move_to_end(call_id)
            while len(self._entries) > self.max_calls:
                self._entries.popitem(last=False)
            return OutputScan(result, full_sanitized, incremental)
