# Agent 输入输出安全控制

本工作区用于设计基于 LiteLLM guardrails、agent-guard 和 Qwen3Guard 的 Agent 输入输出安全控制方案。

当前阶段只包含方案设计，不包含代码实现。

- [完整架构设计](docs/architecture.md)

## 目标目录

- `liteLLM/`：LiteLLM Proxy 配置、guardrail 适配和网关集成。
- `agent-guard/`：静态规则、缓存和 Qwen3Guard 语义分析服务。

推荐的请求路径：

```text
Client -> LiteLLM Proxy -> agent-guard -> Memory/Redis/Qwen3Guard -> Upstream LLM
```
