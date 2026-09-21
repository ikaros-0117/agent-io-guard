# LiteLLM Gateway

本目录直接使用官方 LiteLLM Proxy，不再维护自定义 FastAPI 网关。模型路由、输入/输出 guardrail、鉴权和 OpenAI 兼容 API 均由 LiteLLM 提供，只需要 `config.yaml`。

```text
Client -> LiteLLM Proxy -> agent-guard -> Upstream LLM
```

启动 LiteLLM 前，需先按 [`agent-guard/README.md`](../agent-guard/README.md) 启动 L1 服务，并保证两侧的 `AGENT_GUARD_BASE_URL` 与 `AGENT_GUARD_TOKEN` 一致。

## 配置组成

`config.yaml` 包含三部分：

- `model_list`：将客户端模型名映射到 LiteLLM 支持的供应商模型。
- 顶层 `guardrails`：注册 `generic_guardrail_api`，在 `pre_call` 和 `post_call` 阶段调用 agent-guard。
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
uv run litellm --config config.yaml --host 0.0.0.0 --port 4000
```

也可以直接使用 Docker：

```bash
docker build -t litellm-gateway .
docker run --rm -p 4000:4000 --env-file .env litellm-gateway
```

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

## 配置边界

- 模型 Key 和客户端 Key 都通过环境变量提供。
- `master_key` 只适合单管理员/本地环境；生产环境应使用 LiteLLM 虚拟 Key、团队和预算配置。
- 默认使用 `fail_closed`，agent-guard 不可用时阻止请求。
- 如需检查流式响应，应结合当前 LiteLLM 版本配置 `streaming_end_of_stream_only`、`streaming_sampling_rate` 和 `streaming_transform_mode`。
