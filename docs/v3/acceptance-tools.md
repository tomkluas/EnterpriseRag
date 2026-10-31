# V3 验收工具

`tools/v3/acceptance.py` 提供独立容量测量及人工业务评分汇总。只使用 Python 标准库和已有 httpx；不加载应用配置，不读取 `v3.local`，不直接调用模型接口。工具测试通过不等于生产验收通过。

## 本机离线操作

在仓库根目录的 PowerShell 中：

```powershell
backend/.venv-v3/Scripts/python.exe -m unittest discover -s backend/tests/v3 -p test_acceptance_tools.py
backend/.venv-v3/Scripts/python.exe tools/v3/acceptance.py capacity
backend/.venv-v3/Scripts/python.exe tools/v3/acceptance.py schema
backend/.venv-v3/Scripts/python.exe tools/v3/acceptance.py quality --input frozen-human-scores.json --output quality-report.json
```

`capacity` 默认 dry-run，完全不发请求。`schema` 返回空结构，不能用于通过验收。退出码 0 仅表示当前报告门槛通过；缺证据、dry-run 或未达标返回 2。输入或执行错误返回非零。

## 显式容量测量（会写入测试系统）

以下命令只是使用说明，本轮未执行。仅针对已授权的隔离测试系统使用。实际运行会创建会话、保存问答，并可能经业务系统触发付费模型；不自动清理会话，操作者应按测试环境保留策略处理。

先由安全渠道把 Bearer 凭据放入 `V3_ACCEPTANCE_TOKEN` 环境变量，不要写入配置或命令参数。准备 `capacity-context.json`，仅包含非秘密环境说明：

```json
{
  "host": "测试主机 CPU/内存/磁盘及服务版本",
  "model": "固定模型版本与参数摘要",
  "network": "固定网络路径",
  "original_size": "原件大小分布",
  "document_count": 100,
  "active_chunks": 10000,
  "load_phase": "stable"
}
```

```powershell
backend/.venv-v3/Scripts/python.exe tools/v3/acceptance.py capacity --target https://isolated-test.example --allow-write --space-id TEST_SPACE --duration 1800 --concurrency 10 --context capacity-context.json --output capacity-report.json
```

`--target` 必须显式给出 origin，不允许用户名密码、路径或查询参数；凭据仅从 `--token-env` 指定的环境变量读取。禁止在上下文描述中填写密钥。报告不保存问题、token 正文、HTTP 错误正文或凭据，只保留不可逆配置指纹、环境说明、实际持续时间、请求数和指标。

每个并发工作单元先创建独立会话，再发流式请求；包括会话创建在内的端到端耗时计入测量。超过 `--timeout`（默认 180 秒）算失败。仅非空白 `token` 事件计为首段有效回答；心跳、状态、来源不计。HTTP 失败、error、协议错误、无 done 或无有效 token 均失败。P50/P95 使用成功请求的最近秩分位数，失败单独进入成功率分母，报告明确给出统计口径。至少 10 并发、实际负载持续至少 1800 秒、首段 P95 ≤ 10 秒、完整 P95 ≤ 45 秒、成功率 ≥ 99%，且所有环境字段齐备，才允许容量门槛通过。排空最后请求的时间计入实际 elapsed，不冒充发起负载的持续时间。

该接口没有阶段耗时和队列积压数据，报告明确保留此限制；这类观测、小范围/大量授权文档两种场景、故障注入、企业身份、备份恢复、真实业务验收仍需分别取证。

## 冻结业务题人工评分

输入顶层字段：`dataset_id`、`frozen: true`、`frozen_at`、`representative_confirmed_by`（资料负责人）、`documents`、`cases`。测试集必须在执行前冻结；不能因系统答错改预期答案。工具仅汇总人的判定，不判断事实，也不生成业务题。

每份 `documents` 条目：

```json
{"id":"business-document-id","format":"pdf","supported":true}
```

格式分别为 `pdf`、`scanned_pdf`、`docx`、`xlsx`、`txt`、`markdown`。每道 `cases` 条目：

```json
{
  "id": "business-case-id",
  "document_ids": ["business-document-id"],
  "category": "answerable",
  "expected_answer_ref": "冻结答案清单中的条目引用",
  "evidence_ref": "原文依据的外部记录引用",
  "reviewed": true,
  "reviewer": "资料负责人",
  "outcome": "completed",
  "correct": true,
  "citation_supported": true,
  "refusal_correct": false,
  "location_correct": true,
  "permission_leak": false
}
```

类别必须覆盖 `answerable`、`unanswerable`、`conflict`、`historical`、`table`、`scan`、`multi_turn`、`permission`。所有有答案类别（除无答案、权限题）进入正确率及引用率。`citation_supported` 只有全部重要结论获原文引用支持时才能为 true；该按题保守汇总口径不替代逐结论审核。`location_correct` 只有该题全部涉及文档的定位均核对正确才为 true。多格式题进入涉及的每种格式分母。

至少 200 个唯一业务题、实际涉及至少 20 份唯一代表文件；各类别齐备、各格式有有答案样本、每题有人复核并提供预期答案与依据引用。正确率 ≥ 90%、引用支持率 ≥ 95%、无答案正确拒答率 ≥ 95%、每种格式定位 ≥ 95%，权限题全部完成且零泄漏。错误、超时、安全失败不得填写 `completed`；即使勾选正确拒答也不会得分。遗漏人审、缺覆盖、未支持的资料均阻断通过。不能删除失败题再汇总来通过验收。权限题应完整覆盖被冻结的授权矩阵反例，工具无法独立确认输入的真实性与业务代表性，负责人仍需审核原始记录。

本机测试中的合成评分仅验证计算与阻断规则，不是 200 道真实业务题，也不作为生产验收证据。

## 容量中的可回答样本检查

只返回 `token` 和 `done` 的固定拒答不能证明检索与生成容量。真实容量运行应使用已获准的合成或业务测试问题，并重复提供 `--expect-substring` 指定每个预期片段。例如在上面的显式运行命令中添加 `--question '获准的可回答测试问题' --expect-substring '预期事实一' --expect-substring '预期事实二'`。

工具只在内存拼接回答，要求全部片段（可跨 token）匹配，并收到至少一个非空 `sources` 列表，才将该请求标记 `answer_validated: true`。不匹配或没有来源的请求计失败，进入成功率分母；报告仅记录预期片段的 SHA-256 指纹，不记录片段原文或回答正文。预期不能为空白。

未指定预期仍可做流式协议冒烟检查，但 `answer_validation` 门槛不满足，容量报告不会通过。片段匹配只能避免空检索和固定拒答绕过容量门槛，不能证明事实正确、来源支持结论或权限安全；业务准确率仍依赖冻结题库和资料负责人复核。
