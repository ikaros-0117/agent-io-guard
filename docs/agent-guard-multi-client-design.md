# agent-guard Chat 多客户端兼容与统一安全控制设计

| 项目 | 内容 |
| --- | --- |
| 状态 | Proposed（能力模型路线） |
| 版本 | v0.6 |
| 日期 | 2026-09-26 |
| 范围 | `/v1/chat/completions` 多客户端语义兼容；不扩展协议 |
| 固定上游 | LiteLLM Proxy `generic_guardrail_api` |
| 主要实现 | `agent-guard/` 与 `tools/` |
| 后续协议 | `/v1/responses`、Anthropic、Gemini 不属于本阶段 |

> 代码实施手册：[`chat-multi-client-adaptation-implementation.md`](chat-multi-client-adaptation-implementation.md)。本文件说明为什么采用 Capability 路线、Canonical Envelope 如何表达语义，以及本阶段的安全边界。

## 1. 当前状态与核心结论

Chat Completions 的 P0/P1 已完成：

- Chat-only 入口；
- `texts` / `structured_messages` 对齐校验；
- 结构化工具参数、`role=tool` 检查；
- system/developer 信任策略；
- Chat 流式脱敏、holdback、长流保守增量扫描；
- strict/fail-closed 降级。

当前新增的 M0 夹具也已完成结构契约测试，但它暴露出一个事实：generic Chat 和 DSH 的大部分 LiteLLM 入参完全相同，当前可见字段并不能可靠识别客户端名称。

因此，本阶段不采用以下路线：

```text
一个客户端 = 一个完整 Profile = 一套独立语义逻辑
```

该路线会导致客户端数量增长时维护成本线性甚至超线性增长，而且很多客户端根本没有可区分的可靠信号。

本项目采用：

```text
Chat Protocol Adapter
    -> Capability Extraction
    -> Capability Resolver
    -> Canonical Security Envelope
    -> Scan Scope Policy
    -> Client-agnostic Detector
```

核心原则：

1. **默认先按 Generic Chat 处理。** 普通 `system/user/assistant/tool` 结构不需要客户端专属适配。
2. **Profile 不是客户端清单，而是能力组合。** Profile 只负责把请求映射为能力集合；安全策略不依赖客户端名称。
3. **只有出现新语义，才新增 Capability Adapter。** 例如折叠 runtime context、部分历史、RAG 混入 user、工具链缺失配对。
4. **客户端身份不是信任边界。** `client_id` 只能用于观测、匹配和配置；不能因为“识别为可信客户端”就放宽扫描。
5. **无法判断时 Generic + strict/fail-closed。** 不因识别失败静默放行，也不因不确定而扩大信任范围。
6. **工具安全最终应下沉到工具执行边界。** Chat 层仍需检查 `tool_calls` 和 `role=tool`，但不把所有工具语义都变成客户端特例。

## 2. 三层兼容模型

### 2.1 Chat Protocol Adapter

这一层只解释 `/v1/chat/completions` 的通用字段：

- `messages[]`；
- `role`；
- `content`；
- `tool_calls`；
- `role=tool`；
- LiteLLM 的 `texts`；
- `structured_messages`；
- 顶层 `tool_calls`；
- `tool_call_id` 配对。

Protocol Adapter 不知道 DSH、OpenCode 或 Claude Code，也不把客户端名称写入安全规则。

### 2.2 Capability Extraction

从结构化字段、header、metadata 和可验证的上下文形状中提取能力：

```text
full_history
partial_history
folded_runtime_context
rag_context
stable_turn_marker
tool_chain
client_metadata
```

能力提取结果必须带：

```text
capability_id
capability_version
confidence
reasons
```

`reasons` 只记录结构信号或字段名，不记录原文。

### 2.3 Capability Resolver

Resolver 将能力组合和安全策略连接起来：

```text
普通 Chat
  -> generic_chat + {}

DSH 风格 runtime context
  -> generic_chat + {folded_runtime_context}

部分历史客户端
  -> generic_chat + {partial_history}

工具链消息
  -> generic_chat + {tool_chain}
```

Resolver 不调用 detector，不修改文本，不决定最终 allow/block；它只提供可审计的解释结果。

## 3. Capability 数据模型

建议内部模型包含：

```python
@dataclass(frozen=True, slots=True)
class CapabilityMatch:
    capability_id: str
    capability_version: str
    confidence: Literal["high", "medium", "low"]
    reasons: tuple[str, ...]
```

```python
@dataclass(frozen=True, slots=True)
class ResolvedContext:
    adapter_id: str
    adapter_version: str
    capabilities: tuple[CapabilityMatch, ...]
    fallback: bool
    confidence: Literal["high", "medium", "low"]
```

要求：

- 同一 payload 在同一 Registry 版本下结果稳定；
- 不记录原文；
- `fallback=true` 时必须进入 Generic strict 语义；
- 低置信度不能获得历史豁免；
- Capability 版本进入审计信息和后续缓存键。

## 4. Canonical Security Envelope

Envelope 是安全策略的唯一输入，检测器不直接理解客户端。

每个 item 至少表达：

```text
item_id
origin_ref
text_index
role
origin
authority
trust
scope
turn_id
content_type
mutable
confidence
profile_id / capability_ids
text
scan
```

推荐语义：

| 内容 | origin | trust | mutable | 说明 |
| --- | --- | --- | --- | --- |
| 真人 user | human | untrusted | true | 普通用户输入 |
| system/developer | application | trusted | false | 当前决策为不扫描、不改写 |
| assistant | model | unknown | false/true | 按输出或历史处理 |
| tool call 参数 | model/tool | untrusted | false | 结构化参数命中危险规则直接 block |
| tool result | tool | untrusted | true | 默认按间接注入检查 |
| runtime context | application/context | unknown | false/true | 只有可靠 Capability 才能赋予特定语义 |
| 无法解释的内容 | unknown | unknown | false/true | low confidence，strict |

### 4.1 不变式

1. `texts[i]` 不能依赖未经验证的数组下标映射；
2. 每个可扫描文本必须有 `origin_ref`；
3. 结构化字段中存在但无扁平坐标的 tool call 必须仍进入扫描；
4. `alignment=degraded` 时关闭历史攻击 neutralization；
5. `confidence=low` 时不得放宽历史豁免；
6. `mutable=false` 的 item 不能被 redact；
7. system/developer 的信任策略不能自动继承到折叠为 `role=user` 的内容；
8. 不可确定来源、轮次或工具链关系时，必须 block/review 或严格扫描，不能 silent allow。

## 5. Capability 目录规划

首批只实现真正需要的 Capability：

```text
agent-guard/agent_guard/
  capabilities/
    __init__.py
    base.py
    models.py
    folded_runtime_context.py
    partial_history.py
    tool_chain.py
    rag_context.py
```

### 5.1 `folded_runtime_context`

负责识别已确认的 application/runtime context 被折叠进 `role=user` 的情况：

- 标记 `origin=application`；
- 标记 `authority=context`；
- 标记 `scope=context`；
- 计算最后一个非 synthetic 的真实用户消息；
- 未知前缀不自动信任；
- 版本化规则，不继续扩大全局前缀列表。

### 5.2 `partial_history`

只有有证据表明客户端不会发送完整历史时才启用。不能仅凭消息数量猜测。

默认行为：

- 无法确认历史完整性时，命中硬规则直接 block；
- 不把缺失历史当成“没有风险”；
- 在审计中记录 `history_completeness=unknown`。

### 5.3 `tool_chain`

负责统一处理：

- assistant `tool_calls`；
- 顶层 `tool_calls`；
- `role=tool`；
- `tool_call_id` 配对；
- 缺失配对、重复投影和不可改写参数。

它不是某个客户端的 Profile，所有 Chat 客户端可复用。

### 5.4 `rag_context`

只有能够通过结构、metadata 或可信接入约定识别时才标记为 RAG/context。不能因为文本看起来像文档就自动赋予特殊信任。

默认仍按 untrusted 内容扫描。

## 6. Client Profile 的新定位

Client Profile 只作为 Capability 的组合声明和观测标签：

```text
generic_chat
  -> capabilities=[]

dsh_chat
  -> capabilities=[folded_runtime_context, tool_chain]

some_client_chat
  -> capabilities=[tool_chain, partial_history]
```

Profile 的职责：

- 记录已知客户端行为；
- 组合 Capability；
- 提供夹具和版本；
- 作为观测维度。

Profile 不负责：

- 直接执行 L1 规则；
- 改写通用 detector；
- 因客户端名称改变 trust；
- 在没有可靠证据时猜测消息来源。

新客户端处理原则：

| 新客户端情况 | 处理 |
| --- | --- |
| 普通 Chat 结构 | 直接 Generic Chat，不新增 Profile |
| 仅 header 不同 | 复用 Generic，记录 client metadata |
| 复用已有语义能力 | 复用 Capability 组合 |
| 出现新消息语义 | 新增最小 Capability Adapter |
| 无法判断 | Generic + low confidence + strict |

## 7. 降级和信任边界

### 7.1 header

header 只能是 hint，不能直接成为 trust 来源。只有在网关侧可验证的 client metadata 才能影响 Profile 匹配；未知或被 LiteLLM 隐藏的 header 不得用于安全放行。

### 7.2 unknown

以下情况统一走 Generic strict：

- Capability/Profile 无法识别；
- 多个 Capability 冲突；
- `texts` 与 structured 消息错位；
- current turn 无法确认；
- 历史是否完整无法确认；
- 工具调用无法配对；
- 客户端提供的 identity 只有未经验证的文本。

### 7.3 system/developer

继续遵守既定决策：

- system/developer 建 item；
- `scan=skip`、`trust=trusted`、`mutable=false`；
- 仍参与对齐和审计；
- 折叠为 `role=user` 后只有 Capability 能识别其为 application context，不能自动继承 system 信任。

## 8. 代码实施顺序

本阶段改为：

```text
M0      fixture contract（当前已完成基础部分）
M0.1    fixture 可识别性审计
M1      Capability 领域模型
M2      Generic Chat + Generic strict fallback
M3      folded_runtime_context Capability
M4      Capability 驱动 Envelope Builder
M5      tool_chain / partial_history / rag_context Capability
M6      Chat client matrix + 真实 Chat-only E2E
```

不再采用：

```text
per-client full Profile implementation
```

## 9. 验收标准

多客户端 Chat 阶段完成必须满足：

1. 目标客户端清单和实际路由已冻结；
2. 每个客户端均有脱敏 fixture，或明确记录复用 Generic；
3. 每个 fixture 都能说明是否存在可靠识别信号；
4. Capability Resolver 的 fallback 和低置信度行为有单测；
5. Envelope 中 capability、origin、authority、scope、turn、confidence 可审计；
6. current/history/context/tool 语义有契约测试；
7. Chat-only 真实网关回放通过；
8. 流式场景通过真实 SSE 客户端断言；
9. generic fallback、unknown、degraded 均 fail-closed；
10. P0/P1 回归全部通过；
11. 文档记录 Capability/Profile 版本和未覆盖范围。

## 10. 后续 L2/L3

Capability 模型成熟后再接缓存、Redis、Qwen3Guard：

```text
cache key = policy_version
           + rules_version
           + adapter_version
           + capability_versions
           + profile_version
           + scope
           + context_fingerprint
```

L2/L3 不得绕过 L1 hard block，也不能使用低置信度结果放宽安全策略。
