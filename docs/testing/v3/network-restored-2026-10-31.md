# 网络调整后复验

日期：2026-10-31。旧失败报告保留；本报告记录网络调整后的新结果，不将历史失败覆盖为通过。

## 已确认结果

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 宿主机真实Embedding | HTTP200，1024维，0.281秒 | `network-recheck-2026-10-31.json` |
| 宿主机真实Chat | HTTP200，0.578秒；输入仅合成探针 | 同上 |
| WeKnora真实上传丢回执恢复 | 实际上传一次，自动回读原件校验、恢复绑定，最终任务completed／副本ready，1块 | `auto-reconciliation-network-restored-2026-10-31.json` |
| Dify真实上传丢回执恢复 | 实际上传一次，唯一文件名及原件hash核对，最终任务completed／副本ready，1块 | 同上 |
| 上述探针清理 | 仅删除本轮已知远端回执且GET确认；仅本轮随机schema已清理 | 同上 |
| 精排与引用生成 | 单条合成证据真实精排0.297秒、生成含预期246及引用 | `model-pipeline-network-restored-2026-10-31.json` |
| 代理链路修复后回归 | 182项通过、无失败/错误/跳过，包含真实CONNECT/TLS代理和PostgreSQL | `backend-proxy-fixed-2026-10-31.json` |
| 修复后单请求 | 有效答案1/1，首段2.171秒、完整2.281秒，来源及预期事实核验通过 | 容量辅助脚本的单请求诊断 |
| 修复后60秒预检 | 206/206成功，首段P95 3.641秒、完整P95 3.938秒，无锁/Context/ASGI错误；清理完成 | `capacity-proxy-fixed-2026-10-31.preflight.json` |
| 正式30分钟容量 | 10并发、有效负载1800秒，实际1802.438秒；6241/6241成功，首段P50/P95=2.500/3.453秒，完整P50/P95=2.812/3.781秒；所有门槛通过 | `capacity-proxy-fixed-2026-10-31.json` |
| 正式测试独立清理核验 | 临时读者全部停用，授权恢复，独立API/runner及私有目录无残留，现行版本与路由未改变 | `capacity-proxy-fixed-2026-10-31.cleanup.json` |
| 真实S3准备 | 固定MinIO官方Quay镜像仍返回401，网络调整未解除该权限/来源阻断 | 本轮docker pull退出1；未改用未知镜像 |

正式容量已自然结束，辅助测试进程已清理，没有留给用户继续运行的后台压测。预期为已授权合成现行制度246元，必须有来源、有效正文及done。当前实测仅WeKnora作为活动底座、1份118字节授权文档／1个活动块；不代表Dify的30分钟容量、大授权范围、企业业务质量或完整生产资格。短预检报告因持续时间不足仍为passed=false，不应把该字段误读为请求失败；正式报告passed=true只覆盖所记录场景。

正式测试前后29份backend/app Python源码整体SHA256一致：`1f08a6d0d7f2602e667e0734167f50fdace1721f8f42896c7ddd0067e1080dd6`。全部请求成功，未出现锁超时、跨上下文错误或ASGI异常。该指纹用于说明未提交工作区实际被测源码，不能用Git HEAD代替它。

完整测试后，候选镜像重建成功：`enterpriserag-v3:production-candidate`，manifest list `sha256:33a76031fc0994b7c37d424b2d3d115d26466db4df79257ca5c2e3739d951606`。无网络临时容器确认UID10001、未配置503和生产配置门禁。没有推送、发布或替换平台容器。本机API/Worker已加载代理修复，最终只读检查见`runtime-proxy-fixed-2026-10-31.json`。

网络恢复后额外发现并修复代理CONNECT误判：之前的防透明重发保护把建隧道的CONNECT和业务POST计为两次业务发送，导致召回后精排被拒绝。真实本机CONNECT＋TLS＋POST失败用例复现后，改为CONNECT不计业务次数且不释放业务授权门禁；真正业务正文发送完才释放，第二次真正业务发送仍拒绝，代理取消仍可靠清理。修复前0/580及单请求TransportError诊断分别保留在`capacity-network-restored-2026-10-31.preflight.json`、`capacity-network-restored-2026-10-31.single-diagnostic.json`，没有改门槛或覆盖失败。

本轮解除网络连接阻断，完成单文档30分钟容量与双底座自动对账最终ready验收。真实企业身份、代表性业务样本、扫描件及复杂定位、真实S3/完整平台灾备、大授权范围及Dify持续容量仍未完成；生产verified保持false。
