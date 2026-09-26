from fastapi.testclient import TestClient

from agent_guard.api import create_app
from agent_guard.config import Settings
from agent_guard.detector import StaticRuleDetector
from agent_guard.output_stream import OutputStreamScanner

KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"


class CountingDetector(StaticRuleDetector):
    def __init__(self):
        super().__init__(Settings(token="test"))
        self.scanned_chars = 0

    def check(self, items, *, output_limits=False):
        self.scanned_chars += sum(len(item.text) for item in items)
        return super().check(items, output_limits=output_limits)


def test_incremental_scans_only_new_suffix_after_safe_commas():
    detector = CountingDetector()
    scanner = OutputStreamScanner(detector)
    text = "safe," * 1000
    assert scanner.scan("call-1", [text]).sanitized_texts == (text,)
    for _ in range(20):
        text += "more," * 1000
        assert scanner.scan("call-1", [text]).incremental
    assert detector.scanned_chars == len(text)
    # Duplicate end-of-stream round is answered from cache without regex scan.
    scanner.scan("call-1", [text])
    assert detector.scanned_chars == len(text)


def test_prefix_redaction_preserved_and_split_secret_detected():
    scanner = OutputStreamScanner(StaticRuleDetector(Settings(token="test")))
    first = scanner.scan("call-1", [f"old {KEY}, next sk-proj-"])
    assert first.sanitized_texts == ("old [REDACTED_SECRET], next sk-proj-",)
    second = scanner.scan("call-1", [f"old {KEY}, next {KEY} end"])
    assert second.incremental
    assert second.sanitized_texts == ("old [REDACTED_SECRET], next [REDACTED_SECRET] end",)


def test_ambiguous_content_and_non_append_fall_back_to_full_scan():
    scanner = OutputStreamScanner(StaticRuleDetector(Settings(token="test")))
    for ambiguous in ("escaped &amp; text,", "encoded %20 text,", "-----BEGIN PRIVATE KEY-----\nA,"):
        scanner.scan("call", [ambiguous])
        assert not scanner.scan("call", [ambiguous + " more,"]).incremental
    scanner.scan("call", ["safe,"])
    assert not scanner.scan("call", ["changed,"]).incremental


def test_call_cache_is_isolated_and_bounded():
    scanner = OutputStreamScanner(StaticRuleDetector(Settings(token="test")), max_calls=1)
    scanner.scan("one", [f"{KEY},"])
    scanner.scan("two", ["safe,"])
    # Evicted 'one' must rescan the complete text, never reuse another call's prefix.
    again = scanner.scan("one", [f"{KEY}, later"])
    assert not again.incremental
    assert again.sanitized_texts == ("[REDACTED_SECRET], later",)


def test_api_reuses_incremental_output_and_preserves_litellm_contract():
    client = TestClient(create_app(Settings(token="test")))
    payload = {"input_type": "response", "litellm_call_id": "stream-1"}
    first = client.post("/beta/litellm_basic_guardrail_api", headers={"x-api-key": "test"},
                        json={**payload, "texts": [f"secret: {KEY}, tail sk-proj-"]})
    second = client.post("/beta/litellm_basic_guardrail_api", headers={"x-api-key": "test"},
                         json={**payload, "texts": [f"secret: {KEY}, tail {KEY} end"]})
    assert first.status_code == second.status_code == 200
    assert second.json()["action"] == "GUARDRAIL_INTERVENED"
    assert second.json()["texts"] == ["secret: [REDACTED_SECRET], tail [REDACTED_SECRET] end"]
    assert second.json()["stream_holdback_chars"] == [64]
