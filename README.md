# Agent 输入输出安全控制

本工作区用于设计基于 LiteLLM guardrails、agent-guard 和 Qwen3Guard 的 Agent 输入输出安全控制方案。当前已完成基于官方 LiteLLM Proxy 的最小网关配置，以及 `agent-guard/` 的第一层静态规则检测服务。

## 请求路径

```text
Client -> Chat-only ingress -> LiteLLM Proxy (loopback) -> agent-guard (L1) -> Upstream LLM
```

后续完整目标路径：

```text
Client -> Chat-only ingress -> LiteLLM Proxy -> agent-guard -> Memory/Redis/Qwen3Guard -> Upstream LLM
```

## 本期覆盖范围（2026-09-26 决定）

**本期只对客户端调用 `/v1/chat/completions` 的请求和响应作安全验收与承诺。** `/v1/responses` 后续适配；即使其部分测试已通过，也不属于本期保障范围。客户端按实际调用路由归类，不能只按 Codex、OpenCode、DSH 等名称推断。

**部署边界：对外只运行 `liteLLM/serve_chat_only.py`（或对应 Docker 镜像），它将 LiteLLM 绑定在 `127.0.0.1:4001`，对外入口只放行 Chat 和模型列表。直接运行 `litellm --config config.yaml` 会绕过路由隔离，不属于本期安全部署。**

## 协议边界

- **上游层（LiteLLM → 真实模型）对 guard 不可见**：LiteLLM 已完成归一化，guard 不需要知道模型如何部署，也不为上游差异做适配。
- **下游层（客户端 → LiteLLM）是唯一需要适配的边界**：LiteLLM 以 OpenAI 兼容协议对外服务，但“OpenAI 兼容”是一个家族——Chat Completions（DSH 类客户端）与 Responses API（Codex 类客户端）字段结构不同；本期适配 Chat，Responses 保留后续适配。
- Gemini 不接入；Anthropic 预留。详见 [多协议输入输出适配设计](docs/protocol-adapter-design.md) 第 0 节。

## 当前执行优先级

| 优先级 | 内容 |
| --- | --- |
| P0 | `/v1/chat/completions` 输入侧判定正确性（对齐校验 + 结构化字段补扫） |
| P1 | `/v1/chat/completions` 流式输出的脱敏生效与体积治理 |
| P2 | `/v1/chat/completions` 多客户端能力适配与验收（Generic Chat + Capability Detection） |
| P3–P4 | 工具链策略、span 精确回写、残余风险文档化 |
| 后续 | `/v1/responses` 适配与验收；Anthropic 按需启用 |

**Chat 路由 P0/P1 已完成；M0.1–M6 的能力模型和合成夹具验收已落地，但真实客户端认证尚未完成。** 当前可证明的是 Generic Chat、折叠 runtime context、工具链等已测消息形状通过真实本机 HTTP 链路；不能推断所有 DSH、OpenCode、Claude Code 或 Codex 版本均受保护。Responses 仍不属于本期承诺。

### 实施进度（2026-09-26）

- P0：已增加内部来源映射、对齐校验与严格降级拦截；Chat 交付矩阵 5 项通过，Responses 的 5 项仅作为诊断基线，结构化工具参数与工具结果可在 guard **被调用时**检出。
- P1：主配置已开启 Chat `incremental_diff`（每轮扫描），guard 返回默认 64 字符 holdback；拆分密钥、拆分 PEM 和 10 万字符长流中密钥均通过真实 HTTP Proxy SSE 端到端验证。输出累积扫描上限独立配置，超限返回策略拦截而非 413。
- P2/M0.1–M6：Generic Chat、`folded_runtime_context`、`tool_chain` 已接入 Chat-only 请求链；现有 Generic/DSH-like **16 个合成 fixture** 通过真实 ingress 矩阵，包含 action、Capability、alignment、confidence、resolver fallback、上游消息、调用次数和 SSE 断言。`partial_history` 和 `rag_context` 因缺乏可靠来源证据暂缓。
- 路由隔离：新增精确路径白名单入口（Chat + 模型列表），LiteLLM 后端仅监听回环地址；真实进程测试证明 Responses/未知路由被拒、Chat/SSE 正常。增量扫描按调用 ID 缓存累计文本，在可证明安全的逗号边界只扫新增部分；对 PEM、编码、Unicode、缺失调用 ID 等不确定情况自动回退全量扫描，并保留原输出上限。
- **剩余边界**：只能对外暴露 Chat-only 入口，不能直连私有 LiteLLM；Responses 纯工具项绕过仍为后续协议缺口。M6 的“真实网关”是链路真实，**夹具仍是合成数据**；真实客户端请求采集、负向端到端、完整审计事件与 span 精确回写仍待完成。逐项状态见 [阶段验收记录](docs/chat-multi-client-stage-acceptance.md)。

## 下一步：完成 Chat Completions 多客户端闭环

基础 Capability 实现和合成矩阵已完成。下一步不是再为每个客户端复制一套 Profile，而是用**真实客户端证据**验证它们实际调用的协议、消息组装和 guard 入参，再决定复用 Generic/现有 Capability 或新增最小适配。

计划顺序：

1. 按实际使用量确定目标客户端及版本，先确认它们调用 Chat 还是 Responses/原生接口；只对 Chat 客户端采集脱敏的原始请求及 LiteLLM→guard 投影。
2. 将真实样本与 16 个合成 fixture 对照；已有结构复用 Generic/Capability，新结构才新增最小 Adapter，并补齐负向 fallback/degraded 端到端测试。
3. 明确工具结果、历史完整性和 RAG 来源策略；补充非流式/流式输出以及工具边界的客户端回归。逐客户端验收通过后才列入支持清单。
4. **另立协议阶段**解决 Responses 纯工具项绕过及其输入/输出、流式矩阵；验收通过前保持 Chat-only 路由隔离。Anthropic 按实际需求排期，Gemini 不接入。

## 文档

- [完整架构设计](docs/architecture.md)：整体方案、三层检测流水线、流式与降级策略。
- [agent-guard 多客户端兼容与统一安全控制设计](docs/agent-guard-multi-client-design.md)：Capability、Canonical Context、Generic fallback 与长期路线。
- [Chat 多客户端适配代码级开发文档](docs/chat-multi-client-adaptation-implementation.md)：DeepSeek/代码代理执行用的文件级任务、接口、夹具、测试和验收清单。
- [Chat 多客户端阶段验收记录](docs/chat-multi-client-stage-acceptance.md)：M0–M6 逐项证据、未完成的真实客户端认证与下一步顺序。
- [多协议输入输出适配设计](docs/protocol-adapter-design.md)：协议边界、Canonical Security Envelope、缺陷证据与执行优先级（第 12 章）。
- [LiteLLM 网关实现与运行说明](liteLLM/README.md)
- [agent-guard L1 服务与运行说明](agent-guard/README.md)
- [输入侧 guard 验证工具](tools/README.md)

## 目标目录

- `liteLLM/`：LiteLLM Proxy 配置、guardrail 适配和网关集成。
- `agent-guard/`：静态规则、缓存和 Qwen3Guard 语义分析服务。
