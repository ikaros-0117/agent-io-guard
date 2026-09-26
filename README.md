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

**本期 Chat 路由的 P0 + P1 已完成约定的功能验收。** Chat-only 入口、输入侧拦截、流式脱敏和长流增量扫描通过真实本机 HTTP 链路验证。下一阶段不是再换协议，而是让多个客户端在同一个 `/v1/chat/completions` 接口下都能被正确解释和保护。Responses 不计入本期验收，也不能因现有代码部分支持而宣称已受保护。

### 实施进度（2026-09-26）

- P0：已增加内部来源映射、对齐校验与严格降级拦截；Chat 交付矩阵 5 项通过，Responses 的 5 项仅作为诊断基线，结构化工具参数与工具结果可在 guard **被调用时**检出。
- P1：主配置已开启 Chat `incremental_diff`（每轮扫描），guard 返回默认 64 字符 holdback；拆分密钥、拆分 PEM 和 10 万字符长流中密钥均通过真实 HTTP Proxy SSE 端到端验证。输出累积扫描上限独立配置，超限返回策略拦截而非 413。
- 路由隔离：新增精确路径白名单入口（Chat + 模型列表），LiteLLM 后端仅监听回环地址；真实进程测试证明 Responses/未知路由被拒、Chat/SSE 正常。增量扫描按调用 ID 缓存累计文本，在可证明安全的逗号边界只扫新增部分；对 PEM、编码、Unicode、缺失调用 ID 等不确定情况自动回退全量扫描，并保留原输出上限。
- **仍需注意**：该隔离依赖只对外暴露入口端口，不能直接暴露私有 LiteLLM 端口。Responses 纯工具项绕过问题作为后续适配缺口保留。当前 Chat 多客户端能力还没有全部落地，下一阶段要先完成夹具可识别性审计，再实现 Generic fallback、Capability Resolver 和少量特殊语义 Adapter。详见 [多客户端设计](docs/agent-guard-multi-client-design.md)、[网关说明](liteLLM/README.md) 和 [工具验证说明](tools/README.md)。

## 下一步：完成 Chat Completions 多客户端闭环

本阶段的目标不是支持新的协议，而是让不同客户端在都调用 `/v1/chat/completions` 时，agent-guard 能正确区分：真人输入、历史消息、客户端 runtime context、RAG 内容、工具调用和工具结果。

计划顺序：

1. 收集并冻结 DSH、OpenCode、Claude Code 等实际 Chat 请求夹具；如果 Codex 等客户端被配置为走 Chat，也纳入同一矩阵。
2. 在 agent-guard 内建立 Capability Registry/Resolver，识别 folded runtime context、tool chain、partial history 等语义能力；识别不确定时走 Generic/strict。
3. 将 Capability 结果映射到统一 Envelope，验证 `texts` 与 `structured_messages` 对齐、当前轮边界和 synthetic context。
4. 对每个客户端分别验收输入拦截、历史攻击处理、工具参数/工具结果、密钥脱敏和流式输出。
5. 只有所有目标 Chat 客户端通过矩阵，才宣称“`/v1/chat/completions` 多客户端闭环”；Responses 仍保持关闭。

## 文档

- [完整架构设计](docs/architecture.md)：整体方案、三层检测流水线、流式与降级策略。
- [agent-guard 多客户端兼容与统一安全控制设计](docs/agent-guard-multi-client-design.md)：Capability、Canonical Context、Generic fallback 与长期路线。
- [Chat 多客户端适配代码级开发文档](docs/chat-multi-client-adaptation-implementation.md)：DeepSeek/代码代理执行用的文件级任务、接口、夹具、测试和验收清单。
- [多协议输入输出适配设计](docs/protocol-adapter-design.md)：协议边界、Canonical Security Envelope、缺陷证据与执行优先级（第 12 章）。
- [LiteLLM 网关实现与运行说明](liteLLM/README.md)
- [agent-guard L1 服务与运行说明](agent-guard/README.md)
- [输入侧 guard 验证工具](tools/README.md)

## 目标目录

- `liteLLM/`：LiteLLM Proxy 配置、guardrail 适配和网关集成。
- `agent-guard/`：静态规则、缓存和 Qwen3Guard 语义分析服务。
