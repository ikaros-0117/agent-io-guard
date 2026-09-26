#!/usr/bin/env python3
"""Replay Chat client fixtures through the real public Chat-only ingress."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

TOOLS_DIR = Path(__file__).resolve().parent
REPO = TOOLS_DIR.parent
for path in (TOOLS_DIR, REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tools.fixtures.chat_clients.loader import ChatClientFixture, load_fixtures  # noqa: E402
from verify_input_guard_e2e import Harness, MASTER_KEY, REPO  # noqa: E402

REQUEST_LOG_MARKER = "litellm guardrail request_id="
SENSITIVE_PATTERNS = {
    "openai_like_secret": re.compile(r"sk-proj-[A-Za-z0-9_-]{16,}"),
    "private_key": re.compile(r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "cn_mobile": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
}


@dataclass(frozen=True, slots=True)
class MatrixRow:
    fixture_id: str
    profile: str
    capability: str
    action: str
    alignment: str
    confidence: str
    resolver_fallback: bool
    upstream_calls: int


@dataclass(frozen=True, slots=True)
class GuardAudit:
    adapter_id: str
    alignment: str
    action: str
    capabilities: tuple[str, ...]
    fallback: bool
    confidence: str
    incremental: bool


def _normalized_messages(messages: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(messages, list):
        return ()
    return tuple(
        {"role": message.get("role"), "content": message.get("content")}
        for message in messages
        if isinstance(message, dict)
    )


def _parse_guard_audit(line: str) -> GuardAudit:
    adapter = re.search(r" adapter_id=(\S+)", line)
    alignment = re.search(r" alignment=(\S+)", line)
    action = re.search(r" action=(NONE|BLOCKED|GUARDRAIL_INTERVENED)", line)
    fallback = re.search(r" fallback=(True|False)", line)
    confidence = re.search(r" confidence=(high|medium|low|not_applicable)", line)
    incremental = re.search(r" incremental=(True|False)", line)
    capabilities = re.search(r" capabilities=\(([^)]*)\) reason_codes=", line)
    if not all((adapter, alignment, action, fallback, confidence, incremental, capabilities)):
        raise AssertionError("agent-guard audit log fields are incomplete")
    capability_ids = tuple(re.findall(r"'([^']+)'", capabilities.group(1)))
    return GuardAudit(
        adapter_id=adapter.group(1),
        alignment=alignment.group(1),
        action=action.group(1),
        capabilities=capability_ids,
        fallback=fallback.group(1) == "True",
        confidence=confidence.group(1),
        incremental=incremental.group(1) == "True",
    )


class ChatClientMatrix:
    def __init__(self, harness: Harness) -> None:
        self.harness = harness
        self.agent_log = harness.log_dir / "agent-guard.log"
        self.rows: list[MatrixRow] = []
        self.failures: list[tuple[str, str]] = []

    def run(self) -> int:
        fixtures = sorted(load_fixtures(), key=lambda fixture: fixture.fixture_id)
        for fixture in fixtures:
            if fixture.stream:
                self._run_stream_fixture(fixture)
            else:
                self._run_input_fixture(fixture)

        output = self._render_summary()
        sensitive_failure = self._report_sensitive_failure(output, fixtures)
        if sensitive_failure is not None:
            self.failures.append(("matrix_report", sensitive_failure))
        print(output)
        if self.failures:
            for fixture_id, reason in self.failures:
                print(f"[FAIL] {fixture_id}: {reason}")
            return 1
        return 0

    def _run_input_fixture(self, fixture: ChatClientFixture) -> None:
        self.harness.upstream.reset()
        self.harness.upstream.stream_chunks = []
        log_start = self._log_size()
        messages = fixture.request["structured_messages"]
        status, _ = self._post_chat(messages, request_id=fixture.fixture_id)
        audit = self._wait_for_audit(log_start, input_type="request")
        expected_calls = 0 if fixture.expected_action == "BLOCKED" else 1
        calls = self.harness.upstream.count()

        if audit is None:
            self.failures.append((fixture.fixture_id, "missing request-phase guard audit"))
            return
        if calls != expected_calls:
            self.failures.append(
                (fixture.fixture_id, f"upstream calls {calls} != {expected_calls}")
            )
            return
        if audit.action != fixture.expected_action:
            self.failures.append(
                (fixture.fixture_id, f"action {audit.action} != {fixture.expected_action}")
            )
            return
        if status != (400 if fixture.expected_action == "BLOCKED" else 200):
            self.failures.append(
                (fixture.fixture_id, f"HTTP status {status} does not match action")
            )
            return
        if audit.alignment != "aligned":
            self.failures.append(
                (fixture.fixture_id, f"alignment {audit.alignment} != aligned")
            )
            return
        if audit.adapter_id != fixture.profile_expected:
            self.failures.append(
                (fixture.fixture_id, f"profile {audit.adapter_id} != {fixture.profile_expected}")
            )
            return
        if audit.capabilities != fixture.expected_capabilities:
            self.failures.append(
                (
                    fixture.fixture_id,
                    f"capabilities {audit.capabilities} != {fixture.expected_capabilities}",
                )
            )
            return
        # These well-formed synthetic Chat projections resolve Generic with high
        # confidence. `fallback_required` in the fixture means *client identity*
        # cannot be distinguished; it is not CapabilitySet.fallback, which means
        # the generic protocol adapter itself failed to match.
        if audit.fallback or audit.confidence != "high":
            self.failures.append(
                (fixture.fixture_id, f"resolver fallback/confidence: {audit.fallback}/{audit.confidence}")
            )
            return

        if fixture.expected_action != "BLOCKED":
            actual_messages = _normalized_messages(
                self.harness.upstream_seen().get("messages")
            )
            expected_messages = tuple(fixture.expected_upstream_messages)
            if actual_messages != expected_messages:
                self.failures.append(
                    (
                        fixture.fixture_id,
                        "upstream messages differ from expected projection",
                    )
                )
                return

        self.rows.append(
            MatrixRow(
                fixture_id=fixture.fixture_id,
                profile=audit.adapter_id,
                capability=",".join(audit.capabilities) or "-",
                action=audit.action,
                alignment=audit.alignment,
                confidence=audit.confidence,
                resolver_fallback=audit.fallback,
                upstream_calls=calls,
            )
        )

    def _run_stream_fixture(self, fixture: ChatClientFixture) -> None:
        self.harness.upstream.reset()
        raw_text = fixture.request["texts"][0]
        self.harness.upstream.stream_chunks = self._split_stream(raw_text)
        messages = [{"role": "user", "content": "Say hello"}]
        log_start = self._log_size()
        status, streamed, error_count = self._stream_chat(
            messages,
            request_id=fixture.fixture_id,
        )
        request_audit = self._wait_for_audit(log_start, input_type="request")
        response_audit = self._wait_for_audit(
            log_start,
            input_type="response",
            require_incremental=True,
        )
        expected_content = "".join(fixture.expected_response_texts or ())
        calls = self.harness.upstream.count()

        if request_audit is None or response_audit is None:
            self.failures.append((fixture.fixture_id, "missing request-phase guard audit"))
            return
        if status != 200:
            self.failures.append((fixture.fixture_id, f"SSE HTTP status {status} != 200"))
            return
        if error_count:
            self.failures.append(
                (fixture.fixture_id, f"SSE contained {error_count} error frames")
            )
            return
        if calls != 1:
            self.failures.append((fixture.fixture_id, f"upstream calls {calls} != 1"))
            return
        if streamed != expected_content:
            self.failures.append(
                (fixture.fixture_id, "SSE client content differs from expected redaction")
            )
            return
        if response_audit.action != fixture.expected_action:
            self.failures.append(
                (
                    fixture.fixture_id,
                    f"action {response_audit.action} != {fixture.expected_action}",
                )
            )
            return
        if request_audit.alignment != "aligned":
            self.failures.append(
                (fixture.fixture_id, f"alignment {request_audit.alignment} != aligned")
            )
            return
        if request_audit.adapter_id != fixture.profile_expected:
            self.failures.append(
                (
                    fixture.fixture_id,
                    f"profile {request_audit.adapter_id} != {fixture.profile_expected}",
                )
            )
            return
        if request_audit.capabilities != fixture.expected_capabilities:
            self.failures.append(
                (
                    fixture.fixture_id,
                    f"capabilities {request_audit.capabilities} != {fixture.expected_capabilities}",
                )
            )
            return
        if request_audit.fallback or request_audit.confidence != "high":
            self.failures.append(
                (fixture.fixture_id, f"resolver fallback/confidence: {request_audit.fallback}/{request_audit.confidence}")
            )
            return
        actual_messages = _normalized_messages(
            self.harness.upstream_seen().get("messages")
        )
        if actual_messages != _normalized_messages(messages):
            self.failures.append(
                (fixture.fixture_id, "stream request messages changed before upstream")
            )
            return

        self.rows.append(
            MatrixRow(
                fixture_id=fixture.fixture_id,
                profile=request_audit.adapter_id,
                capability=",".join(request_audit.capabilities) or "-",
                action=response_audit.action,
                alignment=request_audit.alignment,
                confidence=request_audit.confidence,
                resolver_fallback=request_audit.fallback,
                upstream_calls=calls,
            )
        )

    def _post_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        request_id: str,
    ) -> tuple[int, dict[str, Any]]:
        response = httpx.post(
            f"http://127.0.0.1:{self.harness.proxy_port}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {MASTER_KEY}",
                "x-request-id": request_id,
            },
            json={"model": "mock-guard-target", "messages": messages},
            timeout=60,
        )
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {}
        return response.status_code, body

    def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        request_id: str,
    ) -> tuple[int, str, int]:
        streamed: list[str] = []
        error_count = 0
        with httpx.stream(
            "POST",
            f"http://127.0.0.1:{self.harness.proxy_port}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {MASTER_KEY}",
                "x-request-id": request_id,
            },
            json={
                "model": "mock-guard-target",
                "stream": True,
                "messages": messages,
            },
            timeout=90,
        ) as response:
            status = response.status_code
            if status != 200:
                response.read()
                return status, "", error_count
            for line in response.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                frame = json.loads(line.removeprefix("data: "))
                if "error" in frame:
                    error_count += 1
                    continue
                for choice in frame.get("choices", []):
                    streamed.append(
                        choice.get("delta", {}).get("content") or ""
                    )
        return status, "".join(streamed), error_count

    def _wait_for_audit(
        self,
        start: int,
        *,
        input_type: str,
        require_incremental: bool = False,
    ) -> GuardAudit | None:
        deadline = time.time() + 5
        while time.time() < deadline:
            text = self.agent_log.read_text(encoding="utf-8", errors="replace")
            lines = [
                line
                for line in text[start:].splitlines()
                if REQUEST_LOG_MARKER in line and f"input_type={input_type}" in line
            ]
            if lines:
                audits = [_parse_guard_audit(line) for line in lines]
                if require_incremental:
                    audits = [audit for audit in audits if audit.incremental]
                if audits:
                    return audits[-1]
            time.sleep(0.1)
        return None

    def _log_size(self) -> int:
        return self.agent_log.stat().st_size

    @staticmethod
    def _split_stream(text: str) -> list[str]:
        midpoint = max(1, len(text) // 2)
        return [text[:midpoint], text[midpoint:]]

    def _render_summary(self) -> str:
        lines = [
            "fixture_id | profile | capability | action | alignment | confidence | resolver_fallback | upstream_calls",
            "--- | --- | --- | --- | --- | --- | --- | ---",
        ]
        lines.extend(
            " | ".join(
                (
                    row.fixture_id,
                    row.profile,
                    row.capability,
                    row.action,
                    row.alignment,
                    row.confidence,
                    str(row.resolver_fallback),
                    str(row.upstream_calls),
                )
            )
            for row in self.rows
        )
        lines.append(f"total={len(self.rows)} failures={len(self.failures)}")
        return "\n".join(lines)

    @staticmethod
    def _report_sensitive_failure(
        report: str,
        fixtures: list[ChatClientFixture],
    ) -> str | None:
        for label, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(report):
                return f"report contains sensitive pattern class {label}"
        for fixture in fixtures:
            for text in fixture.request.get("texts", []):
                if text and text in report:
                    return "report contains raw fixture text"
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent-guard-python",
        default=str(REPO / "agent-guard/.venv/bin/python"),
    )
    parser.add_argument(
        "--litellm-bin",
        default=str(REPO / "liteLLM/.venv/bin/litellm"),
    )
    parser.add_argument("--keep-logs", action="store_true")
    args = parser.parse_args()
    harness = Harness(args, stream_chunks=[], chat_only=True)
    print("logs:", harness.log_dir)
    try:
        harness.start_agent_guard()
        harness.start_proxy()
        return ChatClientMatrix(harness).run()
    finally:
        harness.shutdown()


if __name__ == "__main__":
    sys.exit(main())
