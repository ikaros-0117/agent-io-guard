# Agent 输入输出安全控制

本工作区用于设计基于 LiteLLM guardrails、agent-guard 和 Qwen3Guard 的 Agent 输入输出安全控制方案。当前已完成基于官方 LiteLLM Proxy 的最小网关配置，以及 `agent-guard/` 的第一层静态规则检测服务。

## 请求路径

```text
Client -> LiteLLM Proxy -> agent-guard (L1) -> Upstream LLM
```

后续完整目标路径：

```text
Client -> LiteLLM Proxy -> agent-guard -> Memory/Redis/Qwen3Guard -> Upstream LLM
```

## 协议边界

- **上游层（LiteLLM → 真实模型）对 guard 不可见**：LiteLLM 已完成归一化，guard 不需要知道模型如何部署，也不为上游差异做适配。
- **下游层（客户端 → LiteLLM）是唯一需要适配的边界**：LiteLLM 以 OpenAI 兼容协议对外服务，但“OpenAI 兼容”是一个家族——Chat Completions（DSH 类客户端）与 Responses API（Codex 类客户端）字段结构不同，必须分别适配。
- Gemini 不接入；Anthropic 预留。详见 [多协议输入输出适配设计](docs/protocol-adapter-design.md) 第 0 节。

## 当前执行优先级

| 优先级 | 内容 |
| --- | --- |
| P0 | OpenAI 家族输入侧判定正确性（对齐校验 + 结构化字段补扫） |
| P1 | OpenAI 流式输出的脱敏生效与体积治理 |
| P2–P4 | 客户端识别（DSH）、工具链与改写质量、残余风险文档化 |
| R1 | Anthropic 按需启用 |

**最小安全闭环 = P0 + P1。** 在这两项完成前，对外只能承诺“输入侧已防护；流式输出侧仅拦截、不脱敏”。

## 文档

- [完整架构设计](docs/architecture.md)：整体方案、三层检测流水线、流式与降级策略。
- [agent-guard 多客户端兼容与统一安全控制设计](docs/agent-guard-multi-client-design.md)：Canonical Context、Profile Registry 与 D0–D6 组件路线。
- [多协议输入输出适配设计](docs/protocol-adapter-design.md)：协议边界、Canonical Security Envelope、缺陷证据与执行优先级（第 12 章）。
- [LiteLLM 网关实现与运行说明](liteLLM/README.md)
- [agent-guard L1 服务与运行说明](agent-guard/README.md)
- [输入侧 guard 验证工具](tools/README.md)

## 目标目录

- `liteLLM/`：LiteLLM Proxy 配置、guardrail 适配和网关集成。
- `agent-guard/`：静态规则、缓存和 Qwen3Guard 语义分析服务。
