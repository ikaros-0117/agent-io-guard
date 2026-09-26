# Chat 多客户端能力适配阶段验收记录

| 项目 | 结论 |
| --- | --- |
| 日期 | 2026-09-26 |
| 分支 | `codex/m1-capability-domain-model` |
| 验收范围 | M0/M0.1–M6 的代码交付及**合成夹具**回放 |
| 当前判定 | 阶段代码与合成夹具验收通过；**客户端专项适配和 `/v1/responses` 适配暂停（SUSPENDED）**，真实客户端认证待投产前恢复 |
| 实施规范 | [`chat-multi-client-adaptation-implementation.md`](chat-multi-client-adaptation-implementation.md) |

> **暂停决策（2026-09-26）**：客户端专项适配与 `/v1/responses` 适配暂时 suspend。当前不继续扩展客户端 Profile、Capability 或 Responses 协议；已有实现、合成夹具和验证脚本保留作回归与预研。后续进入实际投产准备时，再针对届时确定的具体客户端、版本、实际路由和脱敏请求样本逐项测试与微调。

## 1. 阶段与证据

| 阶段 | 提交 | 实际交付 | 判定 |
| --- | --- | --- | --- |
| M0 | `18a931d` | Generic/DSH 各 8 个合成夹具、schema、loader、契约测试 | 基础完成 |
| M0.1 | `dbdc96f` | 比较可见字段；多数 DSH 夹具无法与 Generic 区分；修正匹配期望、增加审计测试 | 完成 |
| M1 | `a202c63` | `CapabilityMatch`、`CapabilitySet`、`TurnBoundary` 和 Envelope item 元数据 | 完成 |
| M2 | `1e83c5b` | Generic Chat adapter、Registry/Resolver、low-confidence fallback 测试 | 完成 |
| M3 | `69846cf` | `folded_runtime_context` 识别及已知/未知前缀测试 | 完成当前夹具范围 |
| M4 | `7aff606` | Resolver → Capability 驱动 Envelope → detector/API 接入；保留 LiteLLM 动作契约 | 完成当前夹具范围 |
| M5 | `d9fcc26` | `tool_chain` 能力、工具调用去重/配对与测试 | **条件完成**；`partial_history`、`rag_context` 无可靠证据，按设计暂缓 |
| M6 | `25fe40d` | 真实 Chat-only ingress → LiteLLM → guard → mock 上游的 16 个合成夹具矩阵，含 SSE | 完成合成夹具范围 |

注意：提交中的 M0 与 M0.1 位于 `main`；M1–M6 目前在上表分支上，**尚未合并到 `main`**。这不是已发布或已上线的声明。

## 2. 本次复验

在上述分支复验：

```bash
agent-guard/.venv/bin/python -m pytest agent-guard/tests -q
agent-guard/.venv/bin/python tools/verify_chat_client_matrix.py
liteLLM/.venv/bin/python tools/verify_input_guard_inprocess.py
liteLLM/.venv/bin/python tools/protocol_matrix.py
liteLLM/.venv/bin/python tools/verify_stream_guard_inprocess.py
```

结果：

- agent-guard **165 项单元测试通过**；
- M6 **16/16 合成夹具通过**、0 失败；测试启动真实 Chat-only HTTP 链路，断言 guard action、能力、alignment、上游调用/消息和流式客户端 SSE；
- M6 现在额外断言请求阶段的 resolver `confidence=high`、`fallback=False`，并在汇总中显示；
- 旧输入契约 **7/7** 通过；Chat/Responses **10 个诊断场景**通过（Responses 的纯工具项 `KNOWN GAP` 仍在）；流式进程内 **4 个场景**通过。

本次没有用 DSH、OpenCode、Claude Code、Codex 的**真实客户端进程**发请求。所谓“真实 E2E”是指**网关/guard/上游链路真实运行**，不是指 fixture 来源于真实客户端。

## 3. Definition of Done 对照

对照实施手册第 11 节：

| 条目 | 状态 | 说明 |
| --- | --- | --- |
| 1. 目标客户端和实际路由冻结 | 未完成 | 只有 Generic 与 DSH-like 合成样本；其他客户端的实际路由未逐一取证 |
| 2. 每个目标客户端有夹具或 Generic 复用证据 | 部分 | Generic/DSH-like 16 个合成夹具；OpenCode、Claude Code、Codex Chat 无真实脱敏样本 |
| 3. fixture 的可识别性说明 | 已完成当前夹具 | M0.1 明确多数请求无法区分 DSH 身份 |
| 4. Resolver fallback/低置信度单测 | 已完成 | M2 单测覆盖；M6 只含有效结构的 high/非 resolver fallback 请求 |
| 5. Envelope 元数据可审计 | 部分 | item 可序列化、请求日志记录 adapter/capability/confidence/ref；尚未形成完整持久审计事件 |
| 6. current/history/context/tool 契约 | 部分 | 合成场景与单测覆盖；部分历史/RAG 缺证据暂缓 |
| 7. Chat-only 真实网关回放 | 已完成当前夹具 | 16 个合成请求投影重放 |
| 8. SSE 客户端断言 | 已完成当前夹具 | `stream_secret` 在真实 HTTP 流中脱敏 |
| 9. Generic/unknown/degraded fail-closed | 部分 | 单测覆盖低置信度/错位；M6 尚无负向 fallback/degraded E2E 行 |
| 10. P0/P1 回归 | 已完成本次复验 | 上述单测、契约与流式测试通过 |
| 11. 版本与未覆盖范围文档化 | 已完成本记录 | 见下方及 capability 源文件常量 |

因此**不能**将阶段代码通过表述为“所有 Chat 客户端已受保护”或“实施手册第 11 节全部通过”。

### 两种 fallback 不要混淆

夹具的 `match_expectation=fallback_required` 表示：**请求没有证据表明它属于 DSH，应使用 Generic Chat，不得强行识别客户端身份**。代码里的 `CapabilitySet.fallback=True` 表示：**Generic Chat 结构匹配本身失败，需要 low-confidence/strict**。目前有效 Chat 合成夹具为 `adapter_id=generic_chat`、`confidence=high`、`CapabilitySet.fallback=False`，包括那些来源标签为 DSH 的不可区分夹具。这不等于“客户端身份已被验证”。

当前 Generic Chat 结构置信度高时仍可能按既有策略清理可对齐的历史攻击；仅“不知道客户端名称”不会自动关闭历史豁免。这与“未知消息语义、未知历史完整性必须严格处理”不是同一判断。真实客户端验证时，必须检查其是否发送完整历史；若无法证明该前提，需单独评审该路由的历史豁免策略，不能把 `confidence=high` 误当作历史完整性的证据。

## 4. 当前实现与边界

- 解析层：Chat-only ingress 只允许 `/v1/chat/completions`；LiteLLM 调用 agent-guard。
- 能力层：`generic_chat`、`folded_runtime_context`、`tool_chain` 已实现。Capability 的命中只表示识别出消息形状，**不证明消息确由某客户端或受信应用产生**。
- M5 未实现：`partial_history` 和 `rag_context`，因为当前没有可验证的客户端/来源信号；不能凭消息数或文本外观推断。
- 策略层：本阶段仍为 L1 静态规则；不是完整工具授权、RAG 信任策略、Redis/L2 或 Qwen3Guard/L3。
- 输出层：Chat 文本流式脱敏已经验证；输出 `tool_call` 增量和其它路由并不因此自动受保护。
- 改写层：span 精确原文坐标回写（A4）尚未完成；归一化脱敏可能改变未命中字符。
- 协议层：`/v1/responses` 继续由 Chat-only 入口隔离，其纯工具项绕过缺口尚未修复；Anthropic/Gemini 未验收。

## 5. 后续验收顺序（当前暂停）

本节顺序不在当前阶段执行。只有进入实际投产准备、且具体客户端/版本/路由已确定后，才恢复并逐项执行；恢复前继续维持 Chat-only 路由隔离，`/v1/responses` 不开放。

1. **真实 Chat 客户端取证**：按客户端及版本记录实际路由、真实请求和 LiteLLM→guard 投影；只保留脱敏后的结构证据。先验证 DSH，再按实际用量验证 OpenCode、Claude Code 和配置为 Chat 的 Codex；不走 Chat 的客户端不计入本阶段。
2. **能力复用判定**：真实请求若等价于 Generic 或已有 Capability，只增加夹具/回归，不增加客户端代码；若出现可验证的新语义，才新增最小 Capability Adapter。
3. **负向及端到端补测**：加入未知前缀、恶意伪造 runtime context、错位、低置信度、缺少工具配对等 E2E；明确被阻断和上游零调用。补充 fallback/confidence 负向行与审计字段校验。
4. **策略与改写质量**：明确工具结果 block/quarantine、完整历史前提、RAG 来源信任；推进 span 精确回写和流式输出 `tool_call` 风险缓解。
5. **再评估新协议**：单独立项 Responses 输入/输出适配，先修纯工具项绕过、构造 Responses 矩阵与流式验收；通过后才调整路由白名单。Anthropic 按实际需求排期，Gemini 当前不接入。

任何阶段都必须维持 Chat-only 路由隔离和既有 P0/P1 回归，不得凭客户端名称或未验证 header 提升内容的信任等级。
