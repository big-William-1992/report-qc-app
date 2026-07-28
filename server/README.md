# 星衍放射质控 REST 服务

将本地质控引擎（`src/engine.py` + `accounts` + `samplelib`）封装为可远程调用的 HTTP 服务。
规范见《接口文档_HTTP_REST.md》（同仓库 `docs/`）。

## 启动

```bash
cd report_qc_app
pip install -r server/requirements.txt
uvicorn server.main:app --host 0.0.0.0 --port 8000
# 开发热重载：uvicorn server.main:app --reload
```

OCR 能力可选：在运行节点额外安装
`pip install rapidocr-onnxruntime Pillow opencv-python-headless numpy`。
未安装时 `/ocr` 返回 `503 OCR_UNAVAILABLE`，不影响 `/qc`、`/samples` 主流程。

## 鉴权

- **内网 / 可信网段**：写操作（创建账号、改规则、写/删样本）带 `X-Emp-Id: <工号>` 头即可。
- **公网**：先 `POST /api/v1/accounts/login` 拿 `token`，后续请求带
  `Authorization: Bearer <token>`。token 为 HMAC-SHA256 签名（无需数据库，
  服务端用 `QC_API_SECRET` 环境变量校验，默认 `change-me-in-prod`，**生产务必改**）。
- 读操作（`/qc/check`、`/qc/rules` GET、`/samples` GET、`/stats/*`）默认开放（纯计算/只读）。

## 关键端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/qc/check` | 单份报告质控（不落库） |
| POST | `/api/v1/qc/batch` | 批量质控（≤50 条） |
| GET/PUT | `/api/v1/qc/rules` | 读取/更新规则配置 |
| POST | `/api/v1/ocr` | 上传图片 OCR（multipart） |
| POST | `/api/v1/ocr/base64` | base64 图片 OCR |
| POST | `/api/v1/accounts` | 创建账号（首个免鉴权引导） |
| POST | `/api/v1/accounts/login` | 登录签发 token |
| GET | `/api/v1/accounts` | 列出账号 |
| POST/GET/DELETE | `/api/v1/samples[/id]` | 样本落库/查询/删除 |
| GET | `/api/v1/stats/error-types` | 错误类型分布 |
| GET | `/api/v1/stats/trend` | 按日趋势 |
| GET | `/api/v1/health` | 健康检查 |

## 部署建议

- `/qc/*` 纯 CPU 标准库，可多副本水平扩展；`/ocr` 依赖模型与显示环境，建议独立部署在
  具备 GPU/桌面环境的网关节点。
- 生产务必设置 `QC_API_SECRET` 环境变量（HMAC 签名密钥）与 `QC_API_TTL`（token 有效期秒）。
- 满足等保三级追溯：所有写操作记录工号 + 接口 + 时间（可在反向代理或网关层统一审计）。
