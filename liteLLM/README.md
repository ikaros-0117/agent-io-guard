# LiteLLM Gateway

本目录使用官方 LiteLLM Proxy 负责模型路由、guardrail 和鉴权；新增一个**只做路由白名单转发**的薄入口，避免直接暴露尚未验收的 Responses 等接口。入口不替代 LiteLLM 的协议处理。

```text
Client -> chat_only_gateway (:4000) -> LiteLLM Proxy (127.0.0.1:4001) -> agent-guard -> Upstream LLM
```

启动 LiteLLM 前，需先按 [`agent-guard/README.md`](../agent-guard/README.md) 启动 L1 服务，并保证两侧的 `AGENT_GUARD_BASE_URL` 与 `AGENT_GUARD_TOKEN` 一致。

## 本期路由范围

**只对客户端 `/v1/chat/completions` 的输入和输出作本期安全承诺。** `/v1/responses` 留待后续适配，现有代码/测试部分通过不代表全路径受保护。**必须通过 `serve_chat_only.py` 启动或使用本目录 Docker 镜像**：`chat_only_gateway.py` 对外只允许 `POST /v1/chat/completions` 与 `GET /v1/models`，其余精确路径均 404；LiteLLM 本体只监听容器/主机内部 `127.0.0.1:4001`。`config.yaml` 本身不封禁路由，不能单独把 LiteLLM 暴露给客户端。

## 配置组成

`config.yaml` 包含三部分：

- `model_list`：将客户端模型名映射到 LiteLLM 支持的供应商模型。
- 顶层 `guardrails`：注册 `generic_guardrail_api`，在 `pre_call` 和 `post_call` 阶段调用 agent-guard；消息差异由 agent-guard 的 Generic Chat + Capability 解释，不在入口层按客户端名称分流。
- `general_settings.master_key`：设置客户端访问 LiteLLM Proxy 时使用的 Bearer Key。

当前配置会把以下请求头转发给 agent-guard：

- `x-request-id`
- `x-trace-id`
- `x-tenant-id`

## 启动

要求 Python 3.11 或更高版本：

```bash
cd "/Users/pets/Projects/Study/input- output- security/liteLLM"
uv sync
cp .env.example .env
set -a
source .env
set +a
uv run python serve_chat_only.py --config config.yaml --host 127.0.0.1 --port 4000 --backend-port 4001
```

也可以直接使用 Docker（镜像入口默认启用 Chat-only，容器只映射 4000）：

```bash
docker build -t litellm-gateway .
docker run --rm -p 4000:4000 --env-file .env litellm-gateway
```

对外只暴露入口 `:4000`；后端 `:4001` 必须保持回环绑定，不能通过容器端口映射、反向代理或本机不可信进程直接访问。客户端向 `/v1/responses`、直通路由及其它未列入白名单的路径请求会被入口拒绝。

## 调用

```bash
curl http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

列出可用模型：

```bash
curl http://127.0.0.1:4000/v1/models \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY"
```

## agent-guard 接口

LiteLLM 当前版本的 `generic_guardrail_api` 会向下面的地址发送请求：

```http
POST <AGENT_GUARD_BASE_URL>/beta/litellm_basic_guardrail_api
x-api-key: <AGENT_GUARD_TOKEN>
Content-Type: application/json
```

请求主体包含：

```json
{
  "input_type": "request",
  "texts": ["user text"],
  "structured_messages": [{"role": "user", "content": "user text"}],
  "tool_calls": null,
  "tools": null,
  "model": "gpt-4o-mini",
  "request_data": {},
  "additional_provider_specific_params": {
    "policy_id": "default-agent-policy"
  }
}
```

期望响应：

```json
{
  "action": "NONE"
}
```

支持的动作：

- `NONE`：允许原内容继续。
- `BLOCKED`：阻止请求或响应，可通过 `blocked_reason` 返回拒绝原因。
- `GUARDRAIL_INTERVENED`：使用响应中的 `texts` 等字段替换被检查内容，用于脱敏或改写。

如果 agent-guard 暂时不可用，可以在 `config.yaml` 中关闭整个 `guardrails` 配置块。若必须保持原有 `/v1/guard/check` 协议，则不能只靠标准配置，需要编写 LiteLLM `CustomGuardrail` 适配器。

## 后续阶段（暂停）：Chat 多客户端闭环

**客户端专项适配与 `/v1/responses` 适配当前暂停（SUSPENDED）**，待实际投产准备时针对具体客户端测试和微调。恢复前，网关层继续维持单一 Chat-only 入口，不为 DSH、OpenCode、Claude Code 等客户端增加多套网关。它们**只有实际调用** `/v1/chat/completions` 才进入本期链路；差异由 guard 的 Generic Chat + Capability Resolver 处理。现有合成夹具已通过真实网关回放，目标客户端的实际请求/路由留待恢复后逐个取证。

恢复后的网关侧工作主要是：

- 为每个目标客户端保存真实请求夹具；
- 记录客户端实际是否发送完整历史、工具消息和 runtime context；
- 将脱敏真实客户端样本加入已有 `tools/verify_chat_client_matrix.py`，并补负向 fallback/degraded E2E；
- 保持 `/v1/responses`、Anthropic 直通路由和其他未验收路径继续拒绝。

## 配置边界

- 模型 Key 和客户端 Key 都通过环境变量提供。
- `master_key` 只适合单管理员/本地环境；生产环境应使用 LiteLLM 虚拟 Key、团队和预算配置。
- 默认使用 `fail_closed`，agent-guard 不可用时阻止请求。
- 主配置已对 OpenAI Chat 流式输出启用 `streaming_transform_mode: incremental_diff` 和 `streaming_sampling_rate: 1`，配合 guard 的 `stream_holdback_chars` 使脱敏后的增量到达客户端。Responses/Anthropic 流式输出不在该模式的承诺范围内。
- `config.local.yaml` 只配置 `pre_call`，不会提供输出侧保护；不要用它验收流式输出。
- 输出超过 guard 配置的独立上限时返回策略拦截（不再冒泡 413）。稳定调用 ID + 安全逗号边界下只扫新增后缀；遇到 PEM、编码/Unicode、无法确定的边界或缺少调用 ID 时保守地全量扫描。不是对任意内容保证恒定扫描开销。
