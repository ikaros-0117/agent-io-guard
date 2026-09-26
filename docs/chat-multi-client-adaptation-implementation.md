# `/v1/chat/completions` 多客户端能力适配代码级开发文档

| 项目 | 内容 |
| --- | --- |
| 状态 | Suspended；M0.1–M6 已实现合成夹具范围，真实客户端专项适配与 Responses 适配暂停 |
| 版本 | v0.3 |
| 日期 | 2026-09-26 |
| 适用路由 | `/v1/chat/completions` |
| 负责模块 | `agent-guard/`、`tools/` |
| 本期不做 | `/v1/responses`、Anthropic、Gemini、L2/L3、Redis、Qwen3Guard |
| 核心路线 | Generic Chat + Capability Detection + Canonical Envelope + strict fallback |

> 本文是代码实施手册。它取代“每个客户端一个完整 Profile”的旧路线。Client Profile 只作为能力组合和观测标签；只有出现新的消息语义时，才新增 Capability Adapter。

> **实施结果与 DoD 逐项判定**见 [`chat-multi-client-stage-acceptance.md`](chat-multi-client-stage-acceptance.md)。下面的 M 任务记录原定开发步骤；任务代码完成不等于真实 DSH/OpenCode/Claude Code 等客户端已获认证。

> **暂停决策（2026-09-26）**：客户端专项适配与 `/v1/responses` 适配暂停。本文保留已完成阶段的代码级记录；后续进入实际投产准备时，再根据具体客户端、版本、实际路由和脱敏请求样本恢复测试、微调和验收。恢复前维持 Chat-only 路由隔离，`/v1/responses` 不开放。

## 1. 目标

P0/P1 已经完成 Chat 通用安全基础：

```text
Chat-only ingress
  -> LiteLLM
  -> agent-guard Envelope + L1
  -> upstream model
```

本阶段要完成的是：不同客户端虽然都调用 `/v1/chat/completions`，但 guard 能正确解释以下语义：

- 真人当前输入；
- 历史消息；
- runtime/application context；
- RAG/文档内容；
- assistant tool call 参数；
- `role=tool` 工具结果；
- 部分历史或失败轮次；
- system/developer 受信内容。

**不要求每个客户端都拥有独立代码。** 目标是：

```text
普通客户端 -> Generic Chat
相同语义差异 -> 复用已有 Capability
新消息语义 -> 新增最小 Capability Adapter
客户端身份未知、但标准 Chat 结构可识别 -> Generic Chat；不推断客户端专属能力
连 Chat 结构/能力也无法可靠解释 -> fallback=true + low confidence + strict
```

## 2. 当前代码基线

实施前阅读：

| 文件 | 当前职责 | 本阶段方向 |
| --- | --- | --- |
| `agent-guard/agent_guard/api.py` | HTTP 入口、鉴权、Capability Resolver/Envelope Builder、动作映射 | 维持 LiteLLM HTTP 契约，扩充真实客户端和负向回归 |
| `agent-guard/agent_guard/envelope.py` | texts/structured/tool_calls → Capability 驱动的 Canonical Items | 只为有证据的新语义扩展 |
| `agent-guard/agent_guard/detector.py` | L1 规则和限制 | 不写客户端特例，只消费 Canonical Items |
| `agent-guard/agent_guard/output_stream.py` | Chat 输出增量扫描 | 保持客户端无关，补充矩阵回归 |
| `tools/fixtures/chat_clients/` | M0/M0.1 的 Generic/DSH-like 合成 fixture 和可识别性审计 | 用真实客户端脱敏样本校验并扩展能力字段 |
| `tools/verify_chat_client_matrix.py` | M6 合成夹具真实 Chat-only 进程回放 | 增加真实客户端和负向 fallback/degraded 场景 |

原 `envelope.py` 的 `SYNTHETIC_USER_PREFIXES` 已迁移到 `folded_runtime_context` Capability。不要再往通用 Envelope 中加入客户端专属前缀。

## 3. 目标代码结构

```text
agent-guard/agent_guard/
  capabilities/
    __init__.py
    base.py                  # Capability 协议和注册接口
    models.py                # CapabilityMatch、CapabilitySet、TurnBoundary
    folded_runtime_context.py
    partial_history.py         # 仅获得可靠历史完整性信号后实现
    tool_chain.py
    rag_context.py             # 仅获得可靠来源信号后实现

  profiles/
    __init__.py
    base.py                  # Chat Protocol Adapter/Profile 最小接口
    generic_chat.py          # Generic Chat fallback
    resolver.py              # Resolver -> CapabilitySet

  envelope.py                # ChatEnvelopeBuilder + Canonical Items
```

测试：

```text
agent-guard/tests/
  test_capabilities.py
  test_capability_resolver.py
  test_envelope_capabilities.py
  test_chat_client_fixture_contract.py

tools/
  fixtures/chat_clients/
  verify_chat_client_matrix.py
```

不要一开始新建 `dsh_chat.py`、`opencode_chat.py`、`claude_code_chat.py` 三套完整逻辑。

## 4. 核心数据模型

### 4.1 CapabilityMatch

```python
@dataclass(frozen=True, slots=True)
class CapabilityMatch:
    capability_id: str
    capability_version: str
    confidence: Literal["high", "medium", "low"]
    reasons: tuple[str, ...]
```

`reasons` 只能记录结构信号、字段名和规则编号，不能记录原文。

### 4.2 CapabilitySet

```python
@dataclass(frozen=True, slots=True)
class CapabilitySet:
    adapter_id: str
    adapter_version: str
    matches: tuple[CapabilityMatch, ...]
    fallback: bool
    confidence: Literal["high", "medium", "low"]

    def has(self, capability_id: str) -> bool: ...
```

典型结果：

```text
普通 Chat:
  fallback=false, capabilities=[]

DSH-like folded context:
  capabilities=[folded_runtime_context, tool_chain]

Chat 结构也无法识别:
  fallback=true, capabilities=[], confidence=low
```

### 4.3 Canonical Item

当前 `SecurityItem` 需要逐步补充：

```text
origin             human | application | model | tool | unknown
authority          user | system | developer | assistant | tool | context | unknown
trust              trusted | untrusted | unknown
mutable            bool
turn_id            str | None
confidence         high | medium | low
capability_ids     tuple[str, ...]
```

保持现有字段兼容：

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

## 5. Capability 设计

### 5.1 `folded_runtime_context`

识别被折叠到 `role=user` 的 application/runtime context：

- 标记 `origin=application`；
- 标记 `authority=context`；
- 标记 `scope=context`；
- 计算最后一个非 synthetic 的真人 user；
- 规则必须版本化；
- 未知前缀不能自动信任。

### 5.2 `tool_chain`

统一处理：

- assistant `tool_calls`；
- 顶层 `tool_calls`；
- `role=tool`；
- `tool_call_id` 配对；
- 重复投影去重；
- 缺失配对；
- 不可改写参数。

这是所有 Chat 客户端可复用的能力，不属于 DSH 专属逻辑。

### 5.3 `partial_history`

只有有证据证明客户端不发送完整历史时才启用。不能仅凭消息条数猜测。

默认：

- 历史完整性 unknown 时，硬拦截命中直接 block；
- 不因缺少历史而放行；
- 审计记录 `history_completeness=unknown`。

### 5.4 `rag_context`

只有结构、metadata 或可信接入约定足以证明时才标记；文本看起来像文档不构成信号。

默认仍按 untrusted 内容扫描。

## 6. Generic Chat 与 Resolver

### 6.1 GenericChat

默认语义：

- `system`/`developer`：trusted、skip、mutable=false；
- `user`：untrusted；
- `assistant`：model/history；
- `tool`：untrusted tool result；
- 不可解释内容：unknown、low confidence；
- alignment degraded：strict/fail-closed；
- low confidence：不享受历史豁免。

### 6.2 Resolver

```python
class CapabilityResolver:
    def resolve(
        self,
        payload: LiteLLMGuardrailRequest,
    ) -> CapabilitySet: ...
```

匹配顺序：

1. 可验证的 header/metadata；
2. 稳定请求结构；
3. role 序列和工具结构；
4. 已确认的 runtime context 结构；
5. fallback 到 Generic Chat。

规则：

- Resolver 不调用 detector；
- Resolver 不改写文本；
- Resolver 不输出原文日志；
- 多个 Capability 冲突时选择更保守结果；
- 客户端身份未知但标准 Chat 结构可解释时，仍可得到 `generic_chat`、`fallback=false`、高协议结构置信度；这**不是**已验证客户端身份或历史完整性；
- Chat 结构不可识别、能力冲突或低置信度时才 `fallback=true`、`confidence=low`、strict；
- `client_id` 只作为观测标签，不能改变 trust。

## 7. Envelope 构建流程

目标流程：

```text
payload
  -> OpenAI Chat Protocol Adapter
  -> CapabilityResolver
  -> ChatEnvelopeBuilder
  -> AlignmentValidator
  -> ScanScopePolicy
  -> StaticRuleDetector
  -> LiteLLM Action Mapper
```

新增 `ChatEnvelopeBuilder`，不要继续扩大 `build_input_envelope()` 参数列表：

```python
class ChatEnvelopeBuilder:
    def build(
        self,
        payload: LiteLLMGuardrailRequest,
        capabilities: CapabilitySet,
    ) -> SecurityEnvelope: ...
```

必须保留 P0 不变式：

- `texts` 与 structured messages 不能按下标盲目对应；
- structured-only tool call 必须扫描；
- 顶层和消息内 tool calls 去重；
- 未映射文本按 unknown/current/untrusted 处理；
- alignment degraded 时关闭历史 neutralization；
- tool call 参数不可安全改写时命中直接 block；
- system/developer 参与对齐和审计，但按既定策略 skip。

## 8. M0.1：夹具可识别性审计

**已完成。** M0 建立 generic/dsh 两组各 8 个合成 fixture；M0.1 发现多数同场景请求无法区分客户端身份，只有特定消息形状可提取 Capability。原验收步骤如下：

1. 比较每个同场景 fixture 的 `texts`、structured、tool_calls、headers、metadata、role 序列；
2. 输出真正存在的差异字段；
3. 对无法区分的场景，不伪造 header，不强制 `dsh_chat` 识别；
4. 将无法区分 DSH 身份的 `profile_expected` 改为 `generic_chat`，并用 `match_expectation=fallback_required` 记录**客户端身份层面**的回退；它不等于 `CapabilitySet.fallback=True`；
5. 对 `system_runtime_context` 单独标记 `folded_runtime_context` 能力；
6. stream response fixture 不重新识别客户端，标记“profile inherited from request”；
7. 通过审计测试后再进入 M1。

M0.1 已交付：

```text
tools/fixtures/chat_clients/README.md
agent-guard/tests/test_chat_client_fixture_contract.py
agent-guard/tests/test_fixture_matchability.py
```

## 9. 代码任务拆分（历史执行清单）

### M1：Capability 领域模型

- [x] 新建 `capabilities/models.py`、`capabilities/base.py`；
- [x] 定义 `CapabilityMatch`、`CapabilitySet`、`TurnBoundary`；
- [x] 给 `SecurityItem` 增加 capability/origin/authority/trust/mutable/confidence 字段；
- [x] 保持现有 detector 和 LiteLLM HTTP 契约；
- [x] 增加模型和序列化单测。

### M2：Generic Chat + fallback Resolver

- [x] 新建 `profiles/generic_chat.py`、`profiles/resolver.py`；
- [x] **Generic Chat 结构也无法识别时**返回 `CapabilitySet.fallback=true`；可识别的标准 Chat 仍为 `fallback=false`；
- [x] 明确 low confidence 不享受历史豁免；
- [x] 增加 resolver 冲突、fallback 等单测；
- [x] 不实现按客户端名称分流的 DSH 专属 Profile。

### M3：`folded_runtime_context` Capability

- [x] 新建 `capabilities/folded_runtime_context.py`；
- [x] 将通用 `SYNTHETIC_USER_PREFIXES` 迁移为能力规则；
- [x] 实现 last non-synthetic user；
- [x] unknown prefix 按低置信度处理；
- [x] 验证历史攻击 neutralization 不影响当前正常输入；
- [x] 不把任意 `role=user` 标记为 trusted。

### M4：Capability 驱动 Envelope

- [x] 新建 `ChatEnvelopeBuilder`；
- [x] 将 api request 分支改为 Resolver -> Builder -> Validator -> Detector；
- [x] 保留 `NONE`、`BLOCKED`、`GUARDRAIL_INTERVENED` 契约；
- [x] 保留 `item_id`、`origin_ref`、`adapter_version` 审计元数据；完整持久审计事件仍待设计；
- [x] 补充 capability、confidence、degraded 单元回归。

### M5：通用能力扩展

- [x] 基于现有**合成 fixture** 实现 `tool_chain`；`partial_history`、`rag_context` 缺可靠来源证据，明确暂缓，而非声称已覆盖；
- [x] 已有通用工具链逻辑抽为 Capability，不创建客户端专属副本；
- [x] 当前未发现可验证的新语义，不新增客户端专属 Adapter；
- [ ] OpenCode/Claude Code/Codex Chat 尚无真实脱敏请求，需取得证据后才能决定是否复用 Generic。

### M6：客户端矩阵和真实网关验收

- [x] 新建 `tools/verify_chat_client_matrix.py`；
- [x] 16 个**合成** fixture 通过真实 Chat-only ingress；
- [x] 断言 action、上游消息、alignment、capability、请求阶段 confidence/resolver fallback 和调用次数；
- [x] 流式场景断言真实 SSE 客户端内容；
- [x] 汇总不包含原始 secret/private key/PII；
- [x] 生成按 capability/profile/fixture/action/alignment/confidence/fallback 的汇总。

## 10. 测试要求

### 10.1 单元测试

必须覆盖：

- Capability match 高/中/低置信度；
- 多 Capability 冲突；
- Generic fallback；
- folded runtime context；
- system/developer trust；
- current turn 和 last non-synthetic user；
- texts/structured 错位；
- structured-only tool call；
- tool chain 配对；
- 历史攻击 neutralization；
- low confidence 不得历史放行；
- item_id/origin_ref/capability 稳定性。

### 10.2 Chat-only E2E

每种能力组合至少覆盖：

| 场景 | 断言 |
| --- | --- |
| normal | 200，上游收到预期内容 |
| current injection | 4xx，上游 0 次调用 |
| historical injection | 历史被替换，当前输入保留 |
| input secret | 上游没有明文 secret |
| tool argument | 执行前 block |
| tool result injection | 默认 block；quarantine 需另行决策 |
| output secret non-stream | 客户端无明文 |
| output secret stream | SSE 客户端无明文 |
| alignment mismatch | strict 拒绝 |

### 10.3 不变量

- 同一 fixture 在同一 Registry 版本下结果稳定；
- Resolver 不记录原文；
- degraded 请求不存在 silent allow；
- tool_call 参数不可改写时不会返回可继续执行的 redact；
- 不同能力组合对同一 Canonical 语义产生相同安全判决；
- 已脱敏文本再次扫描不会恢复原文。

## 11. Definition of Done

客户端专项适配恢复后，多客户端 Chat **正式完成**仍须同时满足下列全部条件。当前逐项状态见 [阶段验收记录](chat-multi-client-stage-acceptance.md)；M6 合成夹具通过不等于本节 DoD 全部完成：

1. 目标客户端清单和实际路由已冻结；
2. 每个客户端都有脱敏 fixture，或明确记录复用 Generic；
3. 每个 fixture 都说明是否有可靠识别信号；
4. Capability Resolver 的 fallback/低置信度行为有单测；
5. Envelope 中 capability、origin、authority、scope、turn、confidence 可审计；
6. current/history/context/tool 语义有契约测试；
7. Chat-only 真实网关回放通过；
8. 流式场景通过真实 SSE 客户端断言；
9. Generic fallback、unknown、degraded 均 fail-closed；
10. 现有 P0/P1 回归全部通过；
11. 文档记录 Capability/Profile 版本和未覆盖范围。

在以上条件全部满足前，对外只能说：

> “Chat 协议基础 P0/P1 已受保护；已验证的能力组合和客户端 fixture 受保护。”

不能说：

> “所有调用 `/v1/chat/completions` 的客户端都已受保护。”

## 12. 给实现代理的执行规则

1. 先阅读本文件第 2、4、5、6、7、8、11 节；
2. 先完成 M0.1 夹具可识别性审计，再实现 Capability 模型；
3. 不把新客户端直接变成完整 Profile；先判断能否复用 Generic/已有 Capability；
4. 每次只完成一个 M 任务；
5. 不修改 LiteLLM 源码，不开放 `/v1/responses`；
6. 不把客户端特例写入 `rules.py`；
7. 不在日志中输出原文；
8. 每次改动后运行现有单测和对应 Chat-only E2E；
9. 最终报告必须列出：改动文件、Capability/Registry 版本、通过 fixture、未覆盖客户端和已知风险。
