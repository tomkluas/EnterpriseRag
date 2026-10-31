# 生产启动配置预检

生产 API 在创建数据库连接和原件存储之前执行本地配置预检。不合格会停止启动，并固定提示修复本文对应的独立 V3 配置。`app.v3.preflight.check_settings(settings, environ=None)` 返回 `passed`、`checks`、`errors`；检查项和错误仅含固定环境字段名与布尔值，不包含连接串、URL、密钥引用的实际名称或密钥值。该方法不联网、不写文件、不修改数据库。

`passed=True` 只说明配置形式和本地凭据引用齐全，不证明企业登录、存储访问、模型能力、底座验证或生产业务验收通过，也不会生成或修改任何底座 `verified` 资格。企业身份实际值、授权样本、代表性资料与目标规模仍须由部署方确认，不能从本机演示配置推定。

## 在哪里修复

直接运行 API 时，修改由 `V3_ENV_FILE` 指定的独立配置文件及进程 Secret 环境变量，重启 API / Worker。容器部署时，修改 `deploy/v3/.env` 对应字段和部署 Secret 配置，再重新创建相关容器。不要修改 `backend/app/v3/config.py` 写死凭据。`load_dotenv(..., override=False)` 不覆盖已有进程变量；修复文件后仍报同一字段时，应同时检查服务管理器或容器实际注入的环境变量。

| 字段 | 生产要求 |
| --- | --- |
| `V3_MODE` | 明确为 `production`；仅 `development` 使用开发流程，拼错的模式会拒绝 |
| `V3_DATABASE_URL` | 独立 PostgreSQL 数据库，使用 `postgresql://` 或 `postgresql+psycopg2://`，包含数据库名；SQLite 不可用于生产 |
| `V3_BOOTSTRAP_TOKEN` | 删除该变量或设为空；生产不得残留开发启动令牌 |
| `V3_OIDC_ISSUER` | 企业确认的固定 HTTPS 签发者地址，不含通配符、查询参数、片段或 URL 用户名密码；必须与企业 JWT 的 `iss` 精确对应 |
| `V3_OIDC_AUDIENCE` | 企业为本应用约定的固定受众，非空、无通配符；必须与 JWT 的 `aud` 对应 |
| `V3_OIDC_JWKS_URL` | 企业确认的固定 HTTPS 公钥地址，无 URL 用户名密码或片段；仍需真实验证密钥轮换、错误 `kid` 和错误签名拒绝 |
| `V3_OBJECTS_URI` | `s3://<桶名>/<独立前缀>`，禁止桶根目录、跨路径和 URL 用户名密码/查询参数；不接受本地目录 |
| `V3_SECRET_S3_ACCESS` / `V3_SECRET_S3_SECRET` | 独立对象存储访问凭据，均非空；预检不会发送请求验证权限 |
| `V3_MODEL_BASE_URL` | Chat 服务 HTTP 或 HTTPS 地址，禁止 URL 中嵌入用户名密码；可信内网模型允许 HTTP，公网部署应由部署网络策略保护 |
| `V3_MODEL_KEY_REF` | 一个 `V3_SECRET_` 开头且仅含大写字母、数字、下划线的环境变量名；该变量必须存在且非空，禁止直接填写密钥本身 |
| `V3_CHAT_MODEL` / `V3_RERANK_MODEL` | 实际部署可调用的 Chat / 精排模型名，均非空；模型存在性与质量需要另外验证 |
| `V3_RERANK_BASE_URL` | 可选；未设置时精排共用 `V3_MODEL_BASE_URL` 和 `V3_MODEL_KEY_REF`；单独设置时要求有效 HTTP/HTTPS 地址 |
| `V3_RERANK_KEY_REF` | 单独设置精排端点时必须为有效 `V3_SECRET_` 环境变量引用且对应值非空；共用 Chat 端点时核验实际使用的 `V3_MODEL_KEY_REF` |

`V3_OBJECTS_DIR` 保留为开发本地目录配置；`Settings.from_env()` 优先使用 `V3_OBJECTS_URI`。生产错误统一报告 `V3_OBJECTS_URI`，应在此字段修复。独立前缀可以保护部署边界，但预检无法证明该前缀没有被别的租户或系统共用，须由运维结合 IAM 策略和存储清单核对。

## 如何判断结果

1. 按错误字段修正独立配置和 Secret 注入。不要将真实值复制到测试报告或截图。
2. 使用 `check_settings` 或运维 CLI 的预检入口检查字段结果；`errors=[]` 且 `passed=True` 表示本地检查通过。生产 API 若仍缺字段，会在 `create_app` 之前停止。
3. 再执行真实企业身份、PostgreSQL、限定前缀读写、模型与精排探针，完成权限反例、恢复、底座资格、目标规模和业务签收。预检不代替这些步骤。

没有设置 `V3_DATABASE_URL` 时，入口仍保留未配置页面和 503 健康状态，不能视为已启动业务服务。`V3_MODE=development` 不强制企业身份、S3 或生产模型字段，既有 `create_app(settings, domain=测试替身)` 调用不受预检影响。

## 离线验证

在仓库根目录 PowerShell 执行，测试仅使用合成配置和环境变量，不读取私有配置文件：

```powershell
$env:PYTHONPATH = 'backend'
backend/.venv-v3/Scripts/python.exe -m unittest discover -s backend/tests/v3 -p test_preflight.py
```
