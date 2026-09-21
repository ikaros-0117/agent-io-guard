from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, Pattern

Validator = Callable[[re.Match[str]], bool]


class RuleAction(StrEnum):
    HARD_BLOCK = "hard_block"
    REDACT = "redact"
    SUSPICIOUS = "suspicious"


@dataclass(frozen=True, slots=True)
class RegexPattern:
    expression: str
    flags: int = re.IGNORECASE
    validator: Validator | None = field(default=None, compare=False)
    compiled: Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "compiled", re.compile(self.expression, self.flags))

    def first_valid_match(self, value: str) -> re.Match[str] | None:
        for match in self.compiled.finditer(value):
            if self.validator is None or self.validator(match):
                return match
        return None

    def sub_valid(self, replacement: str, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            if self.validator is not None and not self.validator(match):
                return match.group(0)
            return replacement

        return self.compiled.sub(replace, value)


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    category: str
    action: RuleAction
    phases: frozenset[str]
    patterns: tuple[RegexPattern, ...]
    description: str
    replacement: str = "[REDACTED]"
    source_types: frozenset[str] | None = None
    risk_level: str = "high"

    def applies_to(self, *, phase: str, source_type: str) -> bool:
        if phase not in self.phases:
            return False
        return self.source_types is None or source_type in self.source_types

    def first_match(self, value: str) -> RegexPattern | None:
        return next(
            (pattern for pattern in self.patterns if pattern.first_valid_match(value)),
            None,
        )


def _valid_cn_id(match: re.Match[str]) -> bool:
    value = re.sub(r"\s+", "", match.group(0)).upper()
    if not re.fullmatch(r"\d{17}[\dX]", value):
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checksum_map = "10X98765432"
    checksum = sum(int(digit) * weight for digit, weight in zip(value[:17], weights)) % 11
    return checksum_map[checksum] == value[-1]


def _valid_luhn(match: re.Match[str]) -> bool:
    digits = re.sub(r"\D", "", match.group(0))
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        checksum += value
    return checksum % 10 == 0


def _p(expression: str, flags: int = re.IGNORECASE, validator: Validator | None = None) -> RegexPattern:
    return RegexPattern(expression=expression, flags=flags, validator=validator)


STATIC_RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="prompt_injection.ignore_instructions",
        category="prompt_injection",
        action=RuleAction.HARD_BLOCK,
        phases=frozenset({"input"}),
        source_types=frozenset({"message"}),
        description="Direct attempts to override prior or system instructions.",
        risk_level="critical",
        patterns=(
            _p(
                r"\b(?:ignore|disregard|forget)\s+(?:(?:all|any)\s+)?"
                r"(?:the\s+)?(?:previous|prior|above|earlier)\s+"
                r"(?:instructions?|prompts?|rules?)\b"
            ),
            _p(
                r"忽略\s*(?:(?:之前|以上|前面|先前|所有|全部)\s*的?\s*){1,3}"
                r"(?:指令|提示|规则|要求)"
            ),
            _p(
                r"无视\s*(?:(?:之前|以上|前面|先前|所有|全部)\s*的?\s*){1,3}"
                r"(?:指令|提示|规则|要求)"
            ),
        ),
    ),
    Rule(
        rule_id="prompt_injection.system_prompt_exfiltration",
        category="prompt_injection",
        action=RuleAction.HARD_BLOCK,
        phases=frozenset({"input"}),
        source_types=frozenset({"message"}),
        description="Requests that explicitly try to reveal system or developer instructions.",
        risk_level="critical",
        patterns=(
            _p(
                r"\b(?:reveal|show|print|repeat|expose|leak)\s+(?:me\s+)?"
                r"(?:the\s+)?(?:system|developer)\s+"
                r"(?:prompt|message|instructions?)\b"
            ),
            _p(r"(?:泄露|输出|展示|告诉我|重复).{0,8}(?:系统提示词?|开发者消息|内部指令)"),
        ),
    ),
    Rule(
        rule_id="prompt_injection.role_override",
        category="prompt_injection",
        action=RuleAction.HARD_BLOCK,
        phases=frozenset({"input"}),
        source_types=frozenset({"message"}),
        description="High-confidence jailbreak or unrestricted-mode role override phrases.",
        risk_level="critical",
        patterns=(
            _p(
                r"\b(?:pretend\s+(?:you\s+are|to\s+be)|act\s+as)\s+"
                r"(?:an?\s+)?(?:unrestricted|uncensored|unfiltered)\b"
            ),
            _p(r"\b(?:enter|enable|activate)\s+(?:developer|jailbreak|dan)\s+mode\b"),
            _p(r"(?:开启|进入|启用).{0,4}(?:开发者模式|越狱模式)"),
        ),
    ),
    Rule(
        rule_id="dangerous_command.destructive",
        category="dangerous_command",
        action=RuleAction.HARD_BLOCK,
        phases=frozenset({"input", "output"}),
        source_types=frozenset({"tool_call"}),
        description="Known destructive or remote-execution command patterns in tool arguments.",
        risk_level="critical",
        patterns=(
            _p(r"\brm\s+-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)[a-z]*\s+/(?=[\s\"'}*]|$)"),
            _p(r"\bmkfs(?:\.[a-z0-9]+)?\b"),
            _p(r"\bdd\s+if=/dev/zero\s+of=/dev/"),
            _p(r"\b(?:curl|wget)\b[^\n|]{0,300}\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b"),
            _p(r"\b(?:nc|ncat|netcat)\b[^\n]{0,100}\s-e\s+/bin/(?:ba)?sh\b"),
            _p(r"\bbash\s+-i\s*>&\s*/dev/tcp/"),
            _p(r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:"),
        ),
    ),
    Rule(
        rule_id="secret.openai_api_key",
        category="secret",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="OpenAI-style API keys.",
        replacement="[REDACTED_SECRET]",
        risk_level="high",
        patterns=(_p(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9][A-Za-z0-9_-]{19,}\b", flags=0),),
    ),
    Rule(
        rule_id="secret.github_token",
        category="secret",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="GitHub personal access and app tokens.",
        replacement="[REDACTED_SECRET]",
        risk_level="high",
        patterns=(_p(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", flags=0),),
    ),
    Rule(
        rule_id="secret.aws_access_key",
        category="secret",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="AWS access key identifiers.",
        replacement="[REDACTED_SECRET]",
        risk_level="high",
        patterns=(_p(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", flags=0),),
    ),
    Rule(
        rule_id="secret.slack_token",
        category="secret",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="Slack access tokens.",
        replacement="[REDACTED_SECRET]",
        risk_level="high",
        patterns=(_p(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", flags=0),),
    ),
    Rule(
        rule_id="secret.jwt",
        category="secret",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="JWT-shaped bearer credentials.",
        replacement="[REDACTED_SECRET]",
        risk_level="high",
        patterns=(
            _p(
                r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
                flags=0,
            ),
        ),
    ),
    Rule(
        rule_id="secret.authorization_header",
        category="secret",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="Bearer credentials copied into message or tool content.",
        replacement="[REDACTED_SECRET]",
        risk_level="high",
        patterns=(_p(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", flags=0),),
    ),
    Rule(
        rule_id="secret.private_key",
        category="secret",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="PEM private key blocks.",
        replacement="[REDACTED_PRIVATE_KEY]",
        risk_level="critical",
        patterns=(
            _p(
                r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----[\s\S]+?"
                r"-----END(?: [A-Z]+)? PRIVATE KEY-----"
            ),
        ),
    ),
    Rule(
        rule_id="secret.assignment",
        category="secret",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="Credential-like key/value assignments with a non-trivial value.",
        replacement="[REDACTED_SECRET]",
        risk_level="high",
        patterns=(
            _p(
                r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|"
                r"password|passwd|pwd)\s*[:=]\s*[\"']?"
                r"[A-Za-z0-9_./+=-]{12,}"
            ),
        ),
    ),
    Rule(
        rule_id="pii.email",
        category="pii",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="Email addresses.",
        replacement="[REDACTED_EMAIL]",
        risk_level="medium",
        patterns=(
            _p(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])"),
        ),
    ),
    Rule(
        rule_id="pii.cn_mobile",
        category="pii",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="Mainland China mobile numbers.",
        replacement="[REDACTED_PHONE]",
        risk_level="medium",
        patterns=(_p(r"(?<!\d)1[3-9]\d{9}(?!\d)"),),
    ),
    Rule(
        rule_id="pii.cn_id",
        category="pii",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="Checksum-valid mainland China resident identity numbers.",
        replacement="[REDACTED_ID]",
        risk_level="high",
        patterns=(_p(r"(?<!\d)\d{17}[\dXx](?!\d)", validator=_valid_cn_id),),
    ),
    Rule(
        rule_id="pii.bank_card",
        category="pii",
        action=RuleAction.REDACT,
        phases=frozenset({"input", "output"}),
        description="Luhn-valid bank card numbers with optional spaces or hyphens.",
        replacement="[REDACTED_CARD]",
        risk_level="high",
        patterns=(
            _p(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)", validator=_valid_luhn),
        ),
    ),
)
