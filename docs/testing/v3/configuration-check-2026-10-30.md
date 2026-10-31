# V3 用户配置检查 · 2026-10-30

结论：**不通过，当前还不能正常完成 RAG 问答。** 数据库、页面与本机身份入口正常；存在明确的精排配置错误和接口适配缺口，模型网络连通验证受阻，知识底座尚未接入。

本轮按实际 `v3.local` 检查，没有改写用户配置、业务实现、平台模型或运行库资料，没有重启服务。真实模型请求只使用合成文本，未打印密钥、完整向量、业务资料或供应商原始错误内容。

## 1. 验收表

| 配置／链路 | 实际检查 | 结果 |
| --- | --- | --- |
| `V3_DATABASE_URL` | 连接本机独立 PostgreSQL 并执行只读 SELECT | 通过 |
| `V3_MODE` | development | 适用于当前本机开发；不是生产配置验收 |
| `V3_BOOTSTRAP_TOKEN` | 已填写、至少 32 字符；实际 `/api/v3/me` 为 200 | 通过当前开发身份入口 |
| `V3_OBJECTS_DIR` | 目录存在 | 通过存在性检查；本轮没有再写入原件 |
| 回答模型 | `qwen3.8-flash`，基础地址为千问平台的 `/compatible-mode/v1` | 模型用途合理、官方有此型号；真实调用连接失败，账号权限和流式兼容性未验证 |
| Embedding | `qwen3.7-text-embedding-flash` | 模型用途合理；直接 `/embeddings` 请求连接失败，输出维度与账号可用性未实测 |
| 精排模型 | `V3_RERANK_MODEL=qwen3.7-text-embedding-flash` | 不通过：填成了向量模型，不能承担 Rerank |
| 精排地址／协议 | 当前精排基础地址为 `/compatible-mode/v1`，V3 追加 `/rerank` | 不通过：不匹配千问官方精排接口；仅更换模型名不足以修复 |
| 密钥 | 回答、精排、Embedding 密钥均已填写 | 只证明存在；连接未到 HTTP 阶段，不能认定正确或错误 |
| `V3_EMBEDDING_DIMENSION` | 空 | 不构成当前 V3 启动错误；程序不读取此字段。平台实际维度仍要另行配置与验证 |
| WeKnora 接入 | V3 业务库 `v3_backends` 为 0 条，非空活动底座路由 0 条 | 尚未接入，无法从 V3 检索真实资料 |
| WeKnora 默认凭据引用 | `V3_SECRET_WK_READ/WRITE/MANAGE` 均未填写 | 当前默认引用不可用；后续需生成受限凭据并登记，可在权限满足时复用只读引用来检查预建库 |
| 运行进程加载配置 | 启动早于本次配置修改；代码只在启动时读取 Settings | 需重启才能确保应用新配置，不会热加载 |

## 2. 已证实问题与修正方向

### 精排模型类型错误

`V3_RERANK_MODEL` 当前与 Embedding 相同。应选择真正的排序模型，例如 `qwen3-rerank`；如选择 `qwen3.7-text-rerank`，需要对应的 DashScope 适配。不能用模型名称中的版本号相近来判断接口相同。

官方依据：[模型分类](https://help.aliyun.com/en/model-studio/embedding-rerank-model/) 明确区分 embedding-flash 与 text-rerank；前者默认维度 1024 只是官方规格，本轮未测到实际向量。

### 当前 V3 缺少千问精排接口适配

`backend/app/v3/chat/models.py` 的 rank 方法按 `base + '/rerank'` 发平铺 JSON，并读取顶层 results。

千问官方提供：

- `qwen3-rerank`：`POST https://maas.qianwenaiapi.com/compatible-api/v1/reranks`。注意路径是 compatible-api，末尾为 reranks。
- `qwen3.7-text-rerank`：`POST https://maas.qianwenaiapi.com/api/v1/services/rerank/text-rerank/text-rerank`，query/documents 嵌套在 input，top_n 等位于 parameters，结果为 output.results。

因此，精排不能直接沿用聊天基础地址，也不能只把完整接口填进现有 BASE_URL（代码还会追加 /rerank）。需要补充对应适配后再修改为匹配的配置。此项属于程序接入工作，不应让用户靠试填地址解决。

官方依据：[OpenAI 兼容重排序](https://platform.qianwenai.com/docs/api-reference/rerank/openai-rerank)、[DashScope 重排序](https://platform.qianwenai.com/docs/api-reference/rerank/dashscope-rerank)。

### 网络连接未建立

三项实际请求都在连接阶段失败，未收到供应商 HTTP 响应；没有证据将失败归咎于密钥、余额或模型未开通。

补充无密钥探针确认：域名 DNS 能解析；向当前地址直连 GET /models 仍为 ConnectTimeout。本机系统代理未启用，既有 127.0.0.1:7897 端口未监听，HTTP(S)_PROXY 等进程环境为空。未启用代理、修改 DNS、关闭证书校验或替换用户的供应商地址。

### 底座及配置加载未完成

WeKnora 服务运行不代表已接入 V3。当前没有登记底座与活动路由；填写 `V3_EMBEDDING_*` 不会自动把模型配置到 WeKnora。

北京时间：V3 服务进程启动于 18:02:11，配置文件最后修改于 19:48:03。当前入口使用 dotenv + 启动时 Settings，修改文件不会热加载。该时间证据表示需要重启确保生效，不用于猜测网络耗时或运行进程中具体旧密钥内容。

## 3. 实际执行与结果

工作目录：`D:\GitHub\EnterpriseRag`。均通过 `backend/.venv-v3/Scripts/python.exe -` 内联脚本执行；有业务意义的每项结果单独判断，不用脚本退出码 0 冒充 API 成功。

| 操作 | 实际结果 |
| --- | --- |
| 脱敏配置、SQLAlchemy 只读查询、端口进程时间检查 | 命令 0；配置存在；DB 可读；底座和活动路由均 0 |
| 实际 `ModelService.stream`，合成问题与一条合成证据 | ConnectError，约 21.61 秒；没有收到有效回答 |
| 实际 `ModelService.rank`，两条合成候选 | ConnectError，约 21.59 秒；没有收到排序结果 |
| 直接 `/embeddings`，一条合成文本 | ConnectError，约 21.59 秒；没有收到向量 |
| 无密钥直连 `/models` 探针 | ConnectTimeout；DNS 解析成功；没有供应商 HTTP 响应 |
| localhost V3 health／配置令牌登录 | HTTP 200／200 |
| WeKnora health／Dify setup | HTTP 200／200；不表示知识链路完成 |
| 既有 WeKnora 诊断库只读核查 | 知识库详情 200、有 embedding_model_id；受限密钥读模型详情 403，未换高权限凭据绕过，因此未确认模型详情是否已改成真实服务 |

本轮没有重跑无关的 45 项基线测试，也未重复触发已知平台默认配置故障；本次重点是用户新配置及模型真实连接。

## 4. 未验证项与下一步

1. 恢复本机到用户指定千问 API 域名的 HTTPS 连通性，重测回答与 Embedding，判断密钥和账号权限。
2. 选择正确精排模型，并补对应千问接口适配后验证排序结果。
3. 在 WeKnora 配置真实 Embedding、匹配实际向量维度，建立并授权业务知识库，再登记到 V3。
4. 重启 V3 API 与 Worker，完成真实上传、索引、发布、带引用问答验证。
5. 以上通过也不自动解决此前独立复验发现的两个业务缺陷，不替代双底座 G0 与完整生产验收。

本轮不能给出“所有参数满足需要、系统已可正常问答”的结论。没有把有效模型名称或已填密钥当作调用成功。
