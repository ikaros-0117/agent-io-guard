# Chat client fixtures

本目录是 `/v1/chat/completions` 多客户端适配的 M0 基线。它只冻结 LiteLLM
generic guardrail 入参形状和通用期望，不实现 Profile Resolver，也不改变现有
strict/fail-closed 策略。

## 范围

- `generic_chat/`：无法获得可靠客户端信号时的安全回退基线。
- `dsh_chat/`：DSH/pi-ai 风格对 `role=user` 折叠 runtime context 的基线。
- 每个客户端包含八类脱敏场景：`normal`、`current_injection`、
  `historical_injection_current_normal`、`system_runtime_context`、`tool_call`、
  `tool_result`、`input_secret`、`stream_secret`。
- 所有值均为合成夹具；不得把真实 token、用户数据、本机路径或原始会话写入 JSON。
- `request` 是 LiteLLM 1.102.0 实际发送给 agent-guard
  `generic_guardrail_api` 的投影，不是公开 `/v1/chat/completions` 请求体。
  输入夹具保留 `texts`、`structured_messages`、`tool_calls` 和脱敏后的
  `request_headers`；流式输出夹具使用 `input_type=response` 的累积 `texts`。

## 统一 schema

每个 JSON 至少包含：

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 当前固定为 `chat-client-fixture/v1`。 |
| `fixture_id` | 必须为 `<client_id>.<scenario>`。 |
| `client_id` | 当前允许 `generic_chat`、`dsh_chat`。 |
| `profile_expected` | 期望该夹具匹配的 Profile；当前与 `client_id` 相同。 |
| `scenario` | 八个固定场景之一。 |
| `request` | guardrail 入参投影，包含 `input_type`、`texts`、`structured_messages`、`tool_calls`、`request_headers` 等字段。 |
| `expected_action` | `NONE`、`BLOCKED` 或 `GUARDRAIL_INTERVENED`。 |
| `expected_upstream_messages` | action 后上游应看到的脱敏消息；`BLOCKED` 使用空数组。 |
| `notes` | 该夹具只记录结构或安全期望，不放原文。 |
| `sanitized` | 必须为 `true`。 |
| `stream` | 流式输出场景为 `true`，其余为 `false`。 |

流式场景额外提供 `expected_response_texts`，记录客户端最终应看到的脱敏文本。
机器可读 schema 见 [schema.json](schema.json)。

## Loader

`loader.py` 使用标准库校验 JSON 语法、必填字段、目录/client 一致性、Profile/Action
枚举、输入或输出请求形状，以及 `texts` 与 `structured_messages` 的顺序对齐。它不导入
agent-guard、不调用规则引擎，也不执行 Profile 推断。

验证全部夹具：

```bash
agent-guard/.venv/bin/python -m pytest agent-guard/tests/test_chat_client_fixture_contract.py -q
```

## M0 边界

这些夹具只定义“后续 Profile/Envelope 实现需要满足的契约”。本轮没有实现：

- Profile Resolver 和客户端识别；
- DSH runtime context 的 Profile 语义；
- fixture 回放、真实上游矩阵和 SSE 客户端断言；
- `/v1/responses` 或其他协议。

因此，除 `generic_chat` 之外的夹具通过只表示结构和期望已冻结，不表示对应 Profile
已上线或已受保护。
