# 新电脑安装与生产部署

新环境使用本文。当前机器的 `start.bat` 继续启动原有源码环境，不会切换数据。新增 `setup.bat` 使用独立 Docker 项目、配置和卷；两种方式的数据不会自动互通。开发模板默认只部署 WeKnora，不启动 Dify。

## 新人本地运行

安装 Docker Desktop（Linux 容器），启动引擎。Windows 自带 PowerShell 5.1 即可；容器模式无需在宿主机安装 Python、Node.js 或 uv。首次构建需要访问镜像仓库和依赖源。PowerShell 7 用户也可直接执行脚本。

在仓库根目录执行：

```powershell
.\setup.bat -Action Init
```

自动生成 `deploy/dev/.env` 中的数据库密码、开发令牌和平台密钥。再次执行 Init 保留现有文件，不覆盖任何值。配置文件已被 Git 忽略；只保存在受控用户目录，不发送到聊天或提交仓库。

编辑该文件中的模型配置：

| 项目 | 字段 |
| --- | --- |
| 回答 | `V3_MODEL_BASE_URL`、`V3_CHAT_MODEL`、`V3_SECRET_MODEL` |
| 精排 | `V3_RERANK_MODEL`；使用独立服务时同时填写 `V3_RERANK_BASE_URL`、`V3_SECRET_RERANK` |
| Embedding | `V3_EMBEDDING_BASE_URL`、`V3_EMBEDDING_MODEL`、`V3_EMBEDDING_DIMENSION`、`V3_SECRET_EMBEDDING`，供平台建模时填写 |

本机模型服务地址在容器中用 `host.docker.internal`，不能用 `127.0.0.1`；同时将明确可信的模型主机填入 `WK_MODEL_HOST_ALLOWLIST`，例如 `host.docker.internal`，以允许 WeKnora 访问该内网地址，不能放开所有地址。云端服务使用服务商地址。密钥含 `$`、`#` 等字符时使用 dotenv 单引号包围，避免 Compose 插值。未填写模型也可以启动页面，但不能完成问答。

```powershell
.\setup.bat -Action Up
```

入口依次构建镜像、检查配置、创建独立数据库与 WeKnora、初始化 V3 数据库、启动 API 与 Worker，并等待就绪；任何命令失败即停止。不删除数据卷。重复 Up 会更新容器，但不重新生成密码。

工作台：<http://127.0.0.1:19000>，使用 `.env` 中的 `V3_BOOTSTRAP_TOKEN` 登录。WeKnora：<http://127.0.0.1:19081>。平台 API 映射为本机 `19080`；V3 容器登记平台时使用 **`http://weknora:8080`**。

### 完成第一条问答

服务启动并不等于知识库已启用。当前仍保留平台配置和真实验收步骤，不自动把未经验证的底座标成可用：

1. 填好上述模型配置后执行 `.\setup.bat -Action Configure`。它会注册独立开发账号，创建回答与 Embedding 模型、启用问题生成的文档库，并生成限定该库的读取／写入凭据，自动写回 `.env`。平台登录账号为 `WK_ADMIN_EMAIL`，密码为 `WK_ADMIN_PASSWORD`，均保存在该私有文件内。
2. 创建资源的进度保存在同目录的 `.weknora-setup.json`，重复执行复用资源，不重复创建。配置发生模型变更时会明确停止，避免旧索引与新模型不一致；需另建知识库并重新验收。若远端创建结果未知，也会停止，先在平台核实资源再修复状态文件，不能直接删状态重试。已有平台账号或知识库可跳过 Configure，手动填写受限凭据和物理库 ID。此入口只用于新建的开发平台，不用于生产账号维护。
3. 重新执行 Up 使配置生效。在 V3 创建业务知识库、登记 WeKnora，地址为 `http://weknora:8080`，填写物理库 ID 与上述凭据的变量名。
4. 按 [实例验证流程](validation.md) 完成真实检查并签收。容器 CLI 调用方式见下文。为业务库准备并激活目标底座，再上传、发布资料和提问。

旧 `enable_local_pilot.py` 是本机双平台专用诊断，含固定地址与临时目录，不能直接用于此环境。不要复用旧机器或其他实例的报告。Configure 不会自动签收合格报告或激活活动底座；平台登记、真实验收和激活仍按上面第 3、4 步执行。

```powershell
# 签收真实报告：报告只读挂载，替换实例 ID 和绝对报告路径
docker compose --env-file deploy/dev/.env -f deploy/dev/compose.yaml --profile migration run --rm -v "D:/reports:/reports:ro" migrate python -m app.v3.cli --env-file /run/v3.env accept-validation --instance <实例ID> --report /reports/validation.json
```

日常操作：

```powershell
.\setup.bat -Action Up     # 启动或配置变更后重建
.\setup.bat -Action Check  # 配置检查与容器状态；不代替真实问答验收
.\setup.bat -Action Logs
.\setup.bat -Action Stop   # 停止整组，保留配置与所有数据
```

## 生产部署与升级

生产仍使用 `deploy/v3/compose.yaml`，默认项目名保持 `enterpriserag-v3`。它只部署应用与业务数据库；知识平台、企业身份、对象存储、域名与 HTTPS 由目标环境提供，不使用开发 Compose。

```powershell
.\setup.bat -Environment Production -Action Init
# 编辑 deploy/v3/.env：按 production-configuration.md 填写企业配置
.\setup.bat -Environment Production -Action Up
```

Up 会先构建并执行生产配置预检，再启动数据库、停止旧 API / Worker、单独迁移，最后启动应用。预检失败不会继续迁移或启动应用。迁移失败时旧应用保持停止，先按日志排查，不自动恢复可能不兼容的旧版本。已有环境升级前按 [运维手册](operations.md) 完成备份并安排维护窗口；迁移不会自动回滚，配置预检不证明迁移兼容性或生产验收通过。不要同时从多台机器执行部署。

有镜像仓库时，在构建机器生成不可变版本标签并推送：

```powershell
docker build -f deploy/v3/Dockerfile -t registry.company.example/enterpriserag:v3.20261102 .
docker push registry.company.example/enterpriserag:v3.20261102
```

服务器 `.env` 设置 `V3_IMAGE=registry.company.example/enterpriserag:v3.20261102`，再运行：

```powershell
.\setup.bat -Environment Production -Action Up -Pull
```

`-Pull` 拉取镜像，不现场构建。仓库登录由部署方在服务器完成。Linux 安装 PowerShell 7 后使用 `pwsh -File tools/v3/deploy.ps1 -Environment Production -Action Up -Pull`。目前未自动创建远程仓库或推送镜像。

Check / Stop / Logs 同样支持 `-Environment Production`。配置文件可通过 `-ConfigFile` 指定绝对路径，Compose 环境注入和 CLI 挂载均读取同一个文件。首次初始化不覆盖旧配置；生产不自动生成公司身份或云资源凭据。

## 配置来源

`weknora-config.yaml` 为 WeKnora 固定提交 `f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7` 的 `config/config.yaml`，未包含本机登录态或密钥；部署使用 v0.9.0 平台镜像。发布生产需按目标环境固定镜像 digest 并验收。健康接口仅检查基础就绪，模型、平台受限检索和权限反例须另外验证。
