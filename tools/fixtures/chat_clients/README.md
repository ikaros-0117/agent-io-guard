# Chat client fixtures

本目录是 `/v1/chat/completions` 多客户端适配的 M0/M0.1/M5/M6 夹具基线。它冻结
LiteLLM generic guardrail 入参形状、可识别性审计结果和 Capability 期望，不代替
Profile Resolver 或规则实现。

## 范围

- `generic_chat/`：无法获得可靠客户端信号时的 Generic Chat 基线。
- `dsh_chat/`：仅用于记录 DSH/pi-ai 风格的入参观察，不等于已能识别 DSH Profile。
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
| `client_id` | fixture 的来源观察标签，当前允许 `generic_chat`、`dsh_chat`。 |
| `profile_expected` | Resolver 应稳定得到的能力组合/回退 Profile；当前所有预期均为 `generic_chat`。 |
| `match_expectation` | `expected`、`fallback_required`、`capability_required` 或 `inherited_from_request`。 |
| `expected_capabilities` | 该 fixture 允许声明的语义能力：`folded_runtime_context` 或 `tool_chain`。 |
| `scenario` | 八个固定场景之一。 |
| `request` | guardrail 入参投影。 |
| `expected_action` | `NONE`、`BLOCKED` 或 `GUARDRAIL_INTERVENED`。 |
| `expected_upstream_messages` | action 后上游应看到的脱敏消息；`BLOCKED` 使用空数组。 |
| `notes` | 该夹具的结构或安全期望，不放原文。 |
| `sanitized` | 必须为 `true`。 |
| `stream` | 流式输出场景为 `true`，其余为 `false`。 |

`match_expectation` 语义：

| 值 | 含义 |
| --- | --- |
| `expected` | `profile_expected` 是请求阶段可直接执行的基线结果。 |
| `fallback_required` | 入参与 Generic 基线相同，缺少客户端专属可靠信号，必须回退 `generic_chat`，不得猜测客户端 Profile。 |
| `capability_required` | 仍不能据此识别客户端专属 Profile，但请求中存在可单独识别的语义 Capability。 |
| `inherited_from_request` | 输出/流式 fixture 不重新识别客户端，沿用对应输入请求阶段的结果。 |

流式场景额外提供 `expected_response_texts`，记录客户端最终应看到的脱敏文本。
机器可读 schema 见 [schema.json](schema.json)。

## M0.1 可识别性审计

审计比较同一场景的 `texts`、`structured_messages`、`tool_calls`、
`request_headers`、`additional_provider_specific_params`、`model` 和
`litellm_version`。结果如下：

| 场景 | Generic/DSH 请求差异字段 | DSH 期望 |
| --- | --- | --- |
| `normal` | 无 | `profile_expected=generic_chat`、`fallback_required` |
| `current_injection` | 无 | `profile_expected=generic_chat`、`fallback_required` |
| `historical_injection_current_normal` | 无 | `profile_expected=generic_chat`、`fallback_required` |
| `system_runtime_context` | `texts`、`structured_messages`；role 序列 `system,user` → `user,user` | `profile_expected=generic_chat`、`capability_required`、`folded_runtime_context` |
| `tool_call` | 无；双方都有通用 `tool_calls` 结构 | `profile_expected=generic_chat`、`capability_required`、`tool_chain` |
| `tool_result` | 无；双方都有通用 `tool_call_id` 配对 | `profile_expected=generic_chat`、`capability_required`、`tool_chain` |
| `input_secret` | 无 | `profile_expected=generic_chat`、`fallback_required` |
| `stream_secret` | 无；输出阶段不重新识别客户端 | `profile_expected=generic_chat`、`inherited_from_request` |

审计结论：

- `system_runtime_context` 是唯一存在客户端间真实请求结构差异的场景，但该差异只支持
  `folded_runtime_context` Capability，不支持客户端身份识别。
- `tool_call` 与 `tool_result` 的结构在双方一致，因此复用通用 `tool_chain`，
  不创建客户端专属副本。
- 其余输入场景没有可靠识别信号，未增加伪造 header，也未强制标记为 `dsh_chat`。
- `stream_secret` 没有新建客户端识别逻辑，明确标记为继承输入阶段 Profile。
- `client_id` 只作为 fixture 的观察来源，不能作为 trust 或安全放行依据。

## Loader 与审计测试

`loader.py` 使用标准库校验 JSON 语法、必填字段、目录/client 一致性、Profile/Action/
Match 枚举、Capability 枚举、输入或输出请求形状，以及 `texts` 与
`structured_messages` 的顺序对齐。它不导入 agent-guard、不调用规则引擎，也不执行
Profile 推断。

验证全部夹具与 M0.1 审计结论：

```bash
agent-guard/.venv/bin/python -m pytest \
  agent-guard/tests/test_chat_client_fixture_contract.py \
  agent-guard/tests/test_fixture_matchability.py -q
```

## M5 Capability 决策

- 实现 `tool_chain`：现有 fixture 已提供 assistant/top-level `tool_calls`、
  `role=tool` 和 `tool_call_id` 的真实结构证据。
- 不实现 `partial_history`：没有夹具或 metadata 证明客户端不发送完整历史。
- 不实现 `rag_context`：没有结构、metadata 或可信接入约定可证明 RAG 来源。
- 不为不可能区分的客户端创建空 Profile；无可靠差异时继续复用 Generic Chat。

## 当前边界

这些夹具定义已经由 `tools/verify_chat_client_matrix.py` 通过真实 Chat-only ingress
回放，包含 action、Capability、alignment、上游消息、调用次数和流式 SSE 断言。
当前仍未覆盖：

- OpenCode、Claude Code、Codex Chat 等尚无脱敏真实夹具的客户端；
- `/v1/responses` 或其他协议。

因此，`dsh_chat` fixture 通过只表示结构和审计期望已冻结，不表示 DSH Profile
已上线或已受保护。
