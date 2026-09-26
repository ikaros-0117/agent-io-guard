# `/v1/chat/completions` 多客户端适配代码级开发文档

| 项目 | 内容 |
| --- | --- |
| 状态 | Ready for implementation |
| 版本 | v0.1 |
| 日期 | 2026-09-26 |
| 适用路由 | `/v1/chat/completions` |
| 负责模块 | `agent-guard/`、`tools/` |
| 关联设计 | [`agent-guard-multi-client-design.md`](agent-guard-multi-client-design.md)、[`protocol-adapter-design.md`](protocol-adapter-design.md) |
| 本期不做 | `/v1/responses`、Anthropic Messages、Gemini、L2/L3、Redis、Qwen3Guard |

> 这是一份**代码实施手册**，不是新的架构设计。它把已有设计转换成可以逐任务执行、逐项验收的开发计划。实现过程中若与当前 LiteLLM 真实 payload 不一致，以锁定版本的实际 payload 和测试结果为准，并同步更新夹具与本文件。

## 1. 要解决的问题

P0/P1 已经解决了 Chat 通用链路的基础安全问题：

```text
Chat-only ingress
  -> LiteLLM
  -> agent-guard Envelope + L1
  -> 上游模型
```

下一阶段要解决的是：**不同客户端虽然都调用 `/v1/chat/completions`，但消息组装方式不同，guard 不能把它们都当成同一种对话。**

需要正确区分：

- 真人当前输入；
- 历史消息；
- 客户端 runtime context；
- RAG/文档内容；
- assistant 的工具调用参数；
- `role=tool` 的工具结果；
- 失败轮次留下的消息；
- system/developer 受信内容。

目标客户端以实际使用 `/v1/chat/completions` 为准，首批候选：

- `generic_chat`：无法识别客户端时的安全回退；
- `dsh_chat`：DSH/pi-ai 风格，可能把 runtime context 折叠为 `role=user`；
- `opencode_chat`：实际请求确认后启用；
- `claude_code_chat`：只有实际走 Chat Completions 时才启用；
- `codex_chat`：只有 Codex 被配置为 Chat Completions 时才启用。

**不能根据客户端名称直接推断协议。** 本文只处理已经进入 `/v1/chat/completions` 的请求；实际走 `/v1/responses` 的客户端不属于本阶段。

## 2. 当前代码基线

实施前先阅读以下文件，不要重复实现已有能力：

| 文件 | 当前职责 | 多客户端阶段的改动方向 |
| --- | --- | --- |
| `agent-guard/agent_guard/api.py` | LiteLLM 请求入口、鉴权、调用 detector、动作映射 | 接入 Profile Resolver 和新的 Envelope Builder；保持 HTTP 契约不变 |
| `agent-guard/agent_guard/envelope.py` | 将 `texts`、`structured_messages`、`tool_calls` 转成安全 item | 拆出协议结构解析与客户端 Profile 语义，不再把客户端特例写死在通用函数中 |
| `agent-guard/agent_guard/detector.py` | L1 规则扫描和输入限制 | 原则上不改客户端逻辑；只消费 Canonical Items |
| `agent-guard/agent_guard/output_stream.py` | Chat 输出累计文本增量扫描 | 保持客户端无关；补充 Profile 回归，不改变扫描器语义 |
| `agent-guard/agent_guard/models.py` | LiteLLM 请求/响应 Pydantic 模型 | 如需新增内部模型，优先放在 `profiles/` 或 `envelope.py`，不要改变 LiteLLM 对外字段 |
| `agent-guard/tests/test_api.py` | guard HTTP 契约测试 | 保留现有测试；增加 profile-aware 场景 |
| `tools/protocol_matrix.py` | Chat/Responses 协议诊断 | Chat 列继续作为协议基线；不把 Responses 变成本期验收 |
| `tools/verify_chat_only_e2e.py` | Chat-only 真实进程端到端 | 扩展为客户端夹具回放入口 |

当前已经存在的通用 synthetic 前缀逻辑位于 `envelope.py` 的 `SYNTHETIC_USER_PREFIXES`。本阶段要把它收敛为 Profile 规则；**不要继续向通用前缀元组追加客户端特例。**

## 3. 目标代码结构

建议新增以下目录，保持与当前项目规模匹配，不要一次引入复杂框架：

```text
agent-guard/agent_guard/
  profiles/
    __init__.py
    base.py              # Profile 协议、匹配结果、公共类型
    models.py            # ProfileMatch、ClientContext、ProfileDecision
    resolver.py          # ProfileResolver，generic fallback
    generic_chat.py      # GenericChatProfile
    dsh_chat.py          # DSHChatProfile
    opencode_chat.py     # OpenCodeChatProfile（确认夹具后启用）
    claude_code_chat.py  # ClaudeCodeChatProfile（确认走 Chat 后启用）
  envelope.py            # 保留统一 Envelope；改为接收 Profile 结果
```

测试和夹具：

```text
tools/
  fixtures/
    chat_clients/
      generic_chat/
        normal.json
        current_injection.json
        historical_injection.json
        tool_result_injection.json
      dsh_chat/
        ...
      opencode_chat/
        ...
      claude_code_chat/
        ...
  verify_chat_client_matrix.py

agent-guard/tests/
  test_profiles.py
  test_profile_resolver.py
  test_profile_envelope.py
```

如果真实客户端请求含敏感信息，夹具必须脱敏；脱敏后必须保留影响结构判断的字段、role 序列、content 类型、工具 ID 关系和 runtime context 形状。

## 4. 核心数据模型

### 4.1 Profile 匹配结果

在 `profiles/models.py` 中定义：

```python
class MatchConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

@dataclass(frozen=True, slots=True)
class ProfileMatch:
    profile_id: str
    profile_version: str
    confidence: MatchConfidence
    reasons: tuple[str, ...]
    fallback: bool = False
```

要求：

- `reasons` 只记录字段名、结构信号和规则 ID，不记录原文；
- 不允许把原始 prompt 写入日志；
- 同一 payload 在相同 Profile Registry 版本下应得到稳定结果；
- 匹配失败时返回 `generic_chat`，而不是 `None`。

### 4.2 Profile 上下文

```python
@dataclass(frozen=True, slots=True)
class ClientContext:
    profile_id: str
    profile_version: str
    confidence: MatchConfidence
    adapter_id: str = "openai-chat-completions"
```

### 4.3 Canonical Item 扩展

当前 `SecurityItem` 已有：

```text
item_id
origin_ref
text_index
role
scope
content_type
text
scan
```

多客户端阶段建议补充：

```text
origin             human | application | model | tool | unknown
authority          user | system | developer | assistant | tool | context | unknown
trust              trusted | untrusted | unknown
mutable            bool
turn_id            str | None
confidence         high | medium | low
profile_id         str
profile_version    str
```

兼容要求：

- `source` 仍然用于 detector 和 LiteLLM 改写坐标；
- `origin_ref` 是审计和定位的主要字段；
- `text_index` 可以为 `None`，表示结构化字段中存在但没有扁平文本回写坐标；
- tool call 参数通常 `mutable=false`；
- tool result 文本可以 `mutable=true`，但是否替换由策略决定；
- `system`/`developer` 按已定决策 `scan=skip`、`mutable=false`，仍进入对齐和审计。

## 5. Profile 接口

在 `profiles/base.py` 定义最小接口：

```python
class ChatClientProfile(Protocol):
    profile_id: str
    profile_version: str

    def match(
        self,
        payload: LiteLLMGuardrailRequest,
    ) -> ProfileMatch: ...

    def classify_message(
        self,
        message: dict[str, Any],
        *,
        message_index: int,
    ) -> MessageClassification: ...

    def classify_content(
        self,
        value: str,
        *,
        message: dict[str, Any],
        message_index: int,
    ) -> ContentClassification: ...

    def current_turn_boundary(
        self,
        messages: list[dict[str, Any]],
    ) -> TurnBoundary: ...
```

公共分类类型至少包含：

```python
@dataclass(frozen=True, slots=True)
class MessageClassification:
    origin: str
    authority: str
    trust: str
    scope_hint: str
    synthetic: bool = False

@dataclass(frozen=True, slots=True)
class TurnBoundary:
    last_assistant_index: int | None
    current_message_indices: frozenset[int]
    confidence: MatchConfidence
    reason: str
```

### 5.1 GenericChatProfile

用途：

- 没有客户端可靠信号时的 fallback；
- 保持当前 Chat 通用行为；
- 不识别客户端专属前缀；
- 对齐失败时严格拒绝。

安全默认值：

- `system`/`developer`：trusted、skip；
- 普通 `user`：untrusted；
- `assistant`：model/history；
- `tool`：untrusted tool result；
- 无法确定来源：`origin=unknown`、`confidence=low`，不享受历史豁免；
- 缺失 `structured_messages`：沿用 strict 行为。

### 5.2 DSHChatProfile

只有完成 DSH 夹具后才能启用 enforce。Profile 负责：

- 识别 DSH 的 runtime context 折叠格式；
- 识别哪些 `role=user` 实际是 application context；
- 计算“最后一个非 synthetic 的真实用户消息”；
- 保持历史攻击 neutralization；
- 未知 synthetic 形态不能默认当作可信，按低置信度 strict 处理。

禁止事项：

- 不把 DSH 前缀添加到通用 L1 规则；
- 不根据文本内容直接把任意 `role=user` 标记为 trusted；
- 不在低置信度时放宽历史豁免。

### 5.3 OpenCodeChatProfile / ClaudeCodeChatProfile

实现前先确认它们的实际 Chat payload。若没有稳定差异：

- 不创建无意义的专用 Profile；
- 使用 `generic_chat`，并在文档记录“当前无独立 Profile 必要”；
- 只有发现 runtime context、历史或工具链组装差异时才新增 Profile。

## 6. Profile Resolver

在 `profiles/resolver.py` 实现：

```python
class ProfileResolver:
    def __init__(self, profiles: Sequence[ChatClientProfile]) -> None: ...

    def resolve(
        self,
        payload: LiteLLMGuardrailRequest,
    ) -> tuple[ChatClientProfile, ProfileMatch]: ...
```

匹配顺序建议：

1. 明确 header/metadata 信号；
2. 稳定的请求结构信号；
3. role 序列和 tool 结构信号；
4. Profile 专属 synthetic/context 结构；
5. fallback 到 `generic_chat`。

匹配规则：

- 多个 Profile 同分时选择更通用的 Profile，并把 `ambiguous_profile` 写入 reason；
- `confidence=low` 不得放宽安全策略；
- Profile Resolver 不调用 detector，不扫描原文；
- Resolver 日志不得包含 prompt 原文；
- Profile 版本进入审计日志和后续缓存键。

## 7. Envelope 构建改造

当前入口流程大致是：

```text
_litellm_items(payload)
  -> detector.check(items)
```

改造后：

```text
payload
  -> ProfileResolver.resolve(payload)
  -> ChatEnvelopeBuilder(profile, match)
  -> AlignmentValidator
  -> Canonical Items
  -> ScanScopePolicy
  -> StaticRuleDetector
  -> LiteLLM Action Mapper
```

建议新增 `ChatEnvelopeBuilder`，不要继续扩大 `build_input_envelope()` 的参数列表：

```python
class ChatEnvelopeBuilder:
    def __init__(self, profile: ChatClientProfile) -> None: ...

    def build(
        self,
        payload: LiteLLMGuardrailRequest,
        match: ProfileMatch,
    ) -> SecurityEnvelope: ...
```

### 7.1 对齐规则

必须保持现有 P0 约束：

- `texts` 与 `structured_messages` 不能按下标盲目对应；
- system/developer 可以只存在于 structured view，但不能导致后续 user 文本错位；
- structured-only tool call 必须进入扫描集合；
- 顶层 `tool_calls` 和消息内 tool calls 必须去重；
- 未映射的扁平文本必须作为 unknown/current/untrusted 处理；
- `alignment=degraded` 时关闭历史豁免；
- `AGENT_GUARD_ALIGNMENT=strict` 时，存在无法解释的结构错位直接 `BLOCKED`。

### 7.2 当前轮边界

Canonical scope 不能只由“最后一个 user”决定。默认规则：

1. 找到最后一条 `assistant` 消息；
2. assistant 之后的内容属于 current turn 候选；
3. 若没有 assistant，使用当前请求中的最后一组可解释 user 内容；
4. DSH Profile 额外剔除已确认的 application synthetic 尾部；
5. 无法确认边界时，硬拦截命中不得按历史 neutralize。

Profile 只能在 canonical boundary 内选择扫描范围，不能修改对齐层的 turn boundary 不变式。

### 7.3 system/developer

遵守现有决策：

- `system` / `developer` 建 item；
- `origin=application`、`trust=trusted`、`scan=skip`、`mutable=false`；
- 不扫描、不改写、不拦截；
- 仍参与 texts/structured 对齐校验和审计；
- 客户端把 context 折叠成 `role=user` 时，不能自动继承该信任策略，必须由 Profile 明确识别。

## 8. 策略与降级

本阶段只实现最小必要策略，不引入完整策略中心。

```python
class ScanScopePolicy(StrEnum):
    FULL = "full"
    CURRENT_TURN = "current_turn"
    FULL_WITH_HISTORY_NEUTRALIZE = "full_with_history_neutralize"
```

默认策略：

| 条件 | 策略 |
| --- | --- |
| Generic/high confidence + 对齐成功 | 按 Chat 默认策略执行 |
| 已知 Profile/high confidence + 对齐成功 | 使用 Profile 策略 |
| Profile low confidence | full scan；不享受历史豁免 |
| alignment=degraded | strict/fail-closed |
| tool call 参数不可改写 | 命中危险规则直接 block |
| tool result 注入 | 默认 block；quarantine 需要单独决策 |

不要在本阶段实现 `shadow` 作为线上放行方式。shadow 可以只用于离线比较，不能用于 degraded 请求。

## 9. 代码任务拆分

### M0：夹具和契约先行

- [ ] 新建 `tools/fixtures/chat_clients/`；建立 generic_chat、dsh_chat 目录。
- [ ] 每个客户端至少准备 normal、current injection、historical injection、system/runtime context、tool call、tool result、secret、stream secret 八类夹具。
- [ ] 定义 fixture schema：`fixture_id`、`client_id`、`profile_expected`、`request`、`expected_action`、`expected_upstream_messages`、`notes`。
- [ ] 所有原文脱敏，不记录真实 token、路径、用户数据。
- [ ] 夹具包含 `structured_messages`、`texts`、`tool_calls`、`request_headers` 的实际形状。

### M1：Profile 领域模型

- [ ] 新建 `agent_guard/profiles/models.py`。
- [ ] 新建 `agent_guard/profiles/base.py`。
- [ ] 定义 `ProfileMatch`、`MatchConfidence`、`MessageClassification`、`TurnBoundary`。
- [ ] 为 `SecurityItem` 增加 profile/origin/authority/trust/mutable/turn/confidence 字段。
- [ ] 保持现有 `TextItem` 和 detector 调用兼容。

### M2：Resolver 和 Generic Profile

- [ ] 新建 `profiles/generic_chat.py` 和 `profiles/resolver.py`。
- [ ] 把当前通用 synthetic 逻辑从 `envelope.py` 拆出。
- [ ] Resolver 无法识别时稳定返回 `generic_chat`。
- [ ] 为 resolver 写 high/medium/low confidence 测试。
- [ ] 对 ambiguous profile 采用 generic/strict，不按第一个注册 Profile 放行。

### M3：DSH Profile

- [ ] 新建 `profiles/dsh_chat.py`。
- [ ] 用 DSH 夹具实现 synthetic runtime context 识别。
- [ ] 实现 last non-synthetic user 计算。
- [ ] 验证历史攻击 neutralization 不影响当前正常输入。
- [ ] 验证 synthetic 尾部中的攻击不能借 unknown prefix 静默放行。
- [ ] 先离线 shadow 对比 generic 结果，再切换到 enforce；shadow 结果不能改变线上 strict 判决。

### M4：Envelope Builder 重构

- [ ] 新建 `ChatEnvelopeBuilder`，将 Profile 分类结果写入 Canonical Items。
- [ ] 将 `api.py` 的 request 分支改为 Resolver -> Builder -> Validator -> Detector。
- [ ] 保留 LiteLLM 三种动作 JSON 契约。
- [ ] 保留现有历史攻击占位符行为。
- [ ] 保留审计字段：`adapter_version`、`origin_ref`、`item_id`。
- [ ] 补充对齐失败和 structured-only tool call 回归。

### M5：OpenCode/Claude Code Profile

- [ ] 先确认实际调用是否为 `/v1/chat/completions`。
- [ ] 如与 generic_chat 无稳定语义差异，复用 generic_chat，不创建空 Profile。
- [ ] 如存在 runtime context、工具链或历史组装差异，再分别创建 Profile。
- [ ] 每个 Profile 都必须有独立 fixture、resolver test、envelope test、Chat-only e2e test。

### M6：客户端矩阵和真实网关验收

- [ ] 新建 `tools/verify_chat_client_matrix.py`。
- [ ] 对每个 fixture 通过真实 Chat-only ingress 发送请求。
- [ ] 断言 guard action、上游消息、输出脱敏和上游调用次数。
- [ ] 流式场景断言客户端收到的 SSE，不只断言 guard 返回值。
- [ ] 输出不包含原始 secret、private key、PII。
- [ ] 生成按 Profile/fixture/action/alignment 的汇总结果。

## 10. 建议的单次提交顺序

不要把所有客户端一次性塞进一个大提交。建议：

1. `test: add Chat client fixture schema and generic contract tests`
2. `feat: add profile domain models and generic resolver`
3. `feat: move synthetic context handling into DSH profile`
4. `refactor: build Chat envelope through profile resolver`
5. `feat: add OpenCode Chat profile`（只有确有差异时）
6. `feat: add Claude Code Chat profile`（只有确有差异时）
7. `test: add Chat client matrix end to end`
8. `docs: record Chat client coverage and profile versions`

每个提交都应保持 `agent-guard/.venv/bin/python -m pytest agent-guard/tests -q` 通过。

## 11. 测试要求

### 11.1 单元测试

必须覆盖：

- Profile match 高/中/低置信度；
- 多 Profile 冲突；
- generic fallback；
- system/developer trust；
- DSH synthetic context；
- last non-synthetic user；
- assistant 之后的 current turn；
- texts/structured 错位；
- structured-only tool call；
- role=tool/tool result；
- 历史攻击 neutralization；
- low confidence 不得历史放行；
- item_id/origin_ref 稳定性。

### 11.2 Chat-only E2E

每个 Profile 至少覆盖：

| 场景 | 断言 |
| --- | --- |
| normal | 200，上游收到原文 |
| current injection | 4xx，上游 0 次调用 |
| historical injection | 200，上游收到占位符和正常当前输入 |
| input secret | 200，上游没有明文 secret |
| tool argument | 4xx，上游 0 次调用 |
| tool result injection | 按策略 block 或 quarantine |
| output secret non-stream | 客户端没有明文 |
| output secret stream | SSE 客户端没有明文 |
| alignment mismatch | strict 模式拒绝 |

### 11.3 性质/不变量测试

- 同一 fixture 在同一 Profile 版本下结果稳定；
- Profile Resolver 不记录原文；
- 任何 degraded 请求不存在 silent allow；
- tool_call 参数不可改写时不会返回可继续执行的 redact；
- 已脱敏文本再次扫描不会恢复原文；
- 不同 Profile 对同一 Canonical 语义请求产生相同安全判决。

## 12. Definition of Done

多客户端 Chat 阶段完成必须同时满足：

1. 目标客户端清单已冻结，并记录实际调用路由；
2. 每个目标客户端都有脱敏 fixture 和 Profile 版本；
3. Resolver 的识别/回退行为有单测；
4. Envelope 中 profile、origin、authority、scope、turn、confidence 可审计；
5. current/history/context/tool 语义有契约测试；
6. 每个 Profile 的 Chat-only E2E 通过；
7. 流式输出经过真实 SSE 客户端断言；
8. generic fallback 和 degraded 均 fail-closed；
9. 现有 P0/P1 回归全部通过；
10. 文档记录已覆盖的客户端/Profile 版本和未覆盖范围。

在以上条件全部满足前，对外只能说：

> “Chat 协议基础 P0/P1 已受保护；已列入清单并通过回归的客户端 Profile 受保护。”

不能说：

> “所有调用 `/v1/chat/completions` 的客户端都已受保护。”

## 13. 给实现代理的执行规则

如果由 DeepSeek 或其他代码代理执行：

1. 先阅读本文件第 2、4、5、7、8、12 节，再开始改代码；
2. 先实现夹具和 generic contract test，再写 Profile；
3. 每次只完成一个 M 任务，不跨越多个未验收阶段；
4. 不修改 LiteLLM 源码，不开放 `/v1/responses`；
5. 不把客户端特例写入 `rules.py`；
6. 不在日志中输出原文；
7. 每次改动后运行单测和对应 Chat-only E2E；
8. 最终报告必须列出：改动文件、Profile 版本、通过的 fixture、未覆盖的客户端和已知风险。
