# Agent 输入输出安全控制

本工作区用于设计基于 LiteLLM guardrails、agent-guard 和 Qwen3Guard 的 Agent 输入输出安全控制方案。

当前阶段已完成基于官方 LiteLLM Proxy 的最小网关配置，以及 `agent-guard/` 的第一层静态规则检测服务。

- [完整架构设计](docs/architecture.md)
- [LiteLLM 网关实现与运行说明](liteLLM/README.md)
- [agent-guard L1 服务与运行说明](agent-guard/README.md)
- [agent-guard 多客户端兼容与统一安全控制设计](docs/agent-guard-multi-client-design.md)

## 目标目录

- `liteLLM/`：LiteLLM Proxy 配置、guardrail 适配和网关集成。
- `agent-guard/`：静态规则、缓存和 Qwen3Guard 语义分析服务。

本期实际请求路径：

```text
Client -> LiteLLM Proxy -> agent-guard (L1) -> Upstream LLM
```

后续完整目标路径：

```text
Client -> LiteLLM Proxy -> agent-guard -> Memory/Redis/Qwen3Guard -> Upstream LLM
```
