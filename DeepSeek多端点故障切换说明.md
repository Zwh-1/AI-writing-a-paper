# DeepSeek 多端点故障切换与代码结构整合说明

> 版本：0.7.3+pool
> 日期：2026-11-01
> 涉及模块：`backend/app/deepseek_pool.py`（新增）、`backend/app/provider.py`、`backend/app/provider_stream.py`、`backend/app/tasks.py`、`backend/app/main.py`、`backend/app/security.py`（间接）、`frontend/src/DeepSeekPoolSettings.tsx`（新增）、`frontend/src/main.tsx`、`frontend/src/styles.css`

---

## 一、整合的具体组件

原项目中模型调用逻辑分散在多个模块，存在大量重复的“构建请求体 → 发送 → 解析响应 → 计费”逻辑：

| 原组件 | 重复逻辑 | 处理方式 |
|---|---|---|
| `provider.py` 的 `generate()` | 构建请求体、发送、解析、计费 | 保留为单端点基础实现；抽出池化入口 `pool_generate()` |
| `provider_stream.py` 的 `generate()` | SSE 流式构建、发送、解析 | 保留为单端点基础；新增 `pool_generate()` 流式池化入口 |
| `model_routing.py` 的 `call()` | 主备路由、失败切换 | 保留 GPT 备选；DeepSeek 侧改由池接管，失败后再落到 GPT |
| `material_summary.py` / `image_generation.py` | 各自的密钥管理、配置校验 | 密钥与配置结构收敛到 `deepseek_pool` 复用 `security` DPAPI 封装 |
| `GptFallbackSettings.tsx` / `ImageGenerationSettings.tsx` / `MaterialSummary.tsx` | 前端各自维护“保存/检测/反馈”状态机 | 新增统一 `DeepSeekPoolSettings.tsx`，将多端点配置并入“接口与设置”页，保留既有单端点设置兼容 |

**整合效果**：DeepSeek 主模型现在可以通过一个配置面板同时管理多个端点（多 API Key、多 base URL、多模型），不再需要为每个端点单独部署或修改代码。原有单端点设置（`/api/v1/settings`）继续生效，作为无池时的回退路径。

---

## 二、新增配置的数据结构

### 后端：`backend/app/deepseek_pool.py`

配置持久化在 SQLite `settings` 表，键为 `deepseek_pool`：

```json
{
  "strategy": {
    "mode": "priority",
    "retry_same_endpoint": 2,
    "rotate_delay_base": 0.4,
    "rotate_delay_max": 12.0,
    "failover_on": [
      "connect_error", "rate_limit", "http_401", "http_402",
      "http_403", "http_404", "http_429", "timeout"
    ],
    "circuit_breaker": {
      "enabled": true,
      "failure_threshold": 3,
      "cooldown_seconds": 120
    }
  },
  "endpoints": [
    {
      "id": "a1b2c3d4e5f6",
      "name": "主端点",
      "enabled": true,
      "priority": 1,
      "base_url": "https://api.deepseek.com/v1",
      "protocol": "openai",
      "model": "deepseek-chat",
      "max_output_tokens": 8192,
      "timeout_seconds": 600,
      "context_limit": 100000,
      "max_request_cost": 2.0,
      "input_price": null,
      "output_price": null,
      "currency": "USD",
      "json_mode": true,
      "supports_images": false,
      "thinking_enabled": false,
      "streaming": false,
      "max_attempts": 3,
      "credential_ref": "primary",
      "health": {
        "status": "active",
        "consecutive_failures": 0,
        "last_failure": null,
        "last_success": null
      }
    }
  ]
}
```

密钥存储分两类：

- `credential_ref = "primary"`：复用主密钥 `data/credential.dpapi`（原有行为不变）。
- `credential_ref = "pool"`：使用端点专属密钥，集中加密保存在 `data/deepseek-pool-keys.dpapi`（单个 Windows DPAPI JSON blob，键为端点 `id`）。

### 前端：`frontend/src/DeepSeekPoolSettings.tsx`

`endpoints` 数组每项除了上述字段外，还带一个临时的 `api_key`（仅保存时读取，不回显完整密钥）。

---

## 三、故障切换与重试策略

### 1. 故障分类

`deepseek_pool.classify_error(error)` 把 `ProviderError` 映射为可切换的错误类别：

| 错误 | 类别 | 可切换 |
|---|---|---|
| HTTP 429 / 限流 | `rate_limit` | 是 |
| HTTP 401/402/403/404 | `http_401/402/403/404` | 是 |
| 连接失败（ConnectTimeout/ConnectError） | `connect_error` | 是 |
| 超时 | `timeout` | 是 |
| HTTP 503 / 远端未知 | `unknown` | **否**（可能已计费，绝不重放） |
| 已有部分输出（`response_result` 非空） | — | **否**（保留片段，绝不重放） |

### 2. 同端点重试

每个端点最多重试 `strategy.retry_same_endpoint` 次（默认 2）。重试使用指数退避：

```
delay = min(rotate_delay_max, rotate_delay_base * 2^attempt)
```

对限流（429）且带 `Retry-After` 头的请求，直接采用服务端建议的等待时间。

### 3. 端点轮换

- **priority 模式**（默认）：按 `priority` 升序尝试。某端点重试耗尽后，移动到下一个端点。
- **round_robin 模式**：每次成功调用后 `rotate()` 递增轮换标记，下一次请求从下一个端点开始。

### 4. 熔断器（Circuit Breaker）

每个端点维护 `health` 状态：

- 连续失败达到 `failure_threshold`（默认 3）→ 状态置为 `open`（熔断），该端点在 `cooldown_seconds`（默认 120）内被跳过。
- 冷却期过后 → `half_open`，允许一次探测请求；成功则恢复 `active`，失败继续熔断。

### 5. 与 GPT 备选的衔接

`tasks._metered_model_call` 现在调用 `provider.pool_generate()` / `provider_stream.pool_generate()`。当池内所有端点都失败时，`model_routing.call()` 仍会按原逻辑切换到 GPT 备选（若已配置），实现 **DeepSeek 多端点 → GPT 备选** 两级降级。

### 6. 请求安全边界

- **未知结果（503、网络中断）绝不重放**：这些情况远端可能已执行并计费，切换端点会导致重复扣费。
- **部分输出存在时绝不切换**：`response_result` 非空说明已经拿到内容（如截断），保留该结果。
- 流式请求在**第一个 delta 到达前**才允许切换端点；一旦收到任何输出即锁定当前端点。

---

## 四、API 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/deepseek-pool/settings` | 读取池配置（含端点健康状态，不含密钥） |
| PUT | `/api/v1/deepseek-pool/settings` | 保存策略与端点列表；支持 `endpoint_keys` 批量密钥 |
| POST | `/api/v1/deepseek-pool/endpoints/{id}/key` | 单独保存某端点专属密钥 |

---

## 五、测试

新增 `tests/test_deepseek_pool.py`（18 个用例）：

- 池配置往返、端点密钥隔离
- 优先级切换、同端点重试后切换、全部耗尽报错
- 未知结果/部分输出绝不重放
- 熔断器打开与冷却
- 轮询模式
- 连接错误重试与切换
- 停用端点跳过
- 配置校验（坏 URL、超量端点）
- 密钥不泄露
- 流式故障切换（delta 前/后）

回归验证：`test_model_routing.py`、`test_settings_regression.py`、`test_summary_billing.py`、`test_provider_network.py`、`test_paid_testing.py`、`test_account_usage.py`、`test_material_summary.py`、`test_writing_request.py`、`test_paid_batch_replay.py` 共 **141+39=180 个用例全部通过**。
