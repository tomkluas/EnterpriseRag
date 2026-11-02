# EnterpriseRag V3 落地方案

V3 面向企业内部集中部署。应用自己掌握原始资料、权限、会话与业务版本，首版同时建设 WeKnora 与 Dify 两套知识适配器，管理员按业务知识库切换已就绪、已验收的底座。

核查日期：2026-10-30。候选版本：WeKnora **v0.9.0**，提交 `f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7`；Dify **1.18.0**，提交 `50abea97062110044f15d0e03ccecadd9ccc87ca`。

**当前状态：本机文档试运行版已可用，59 项后端测试与 2 项前端测试通过，真实模型、双底座往返切换、版本发布和带引用问答已验证。租约失效处理及失败草稿取消恢复已修复验证。基础 DOCX／XLSX／文字 PDF 两套平台共六组解析测试通过。完整 G0、扫描件、复杂表格、精确定位、容量和完整恢复仍未完成，不能批准生产上线。** 最新证据见 [可用版本报告](../testing/v3/live-pilot/README.md)，平台问题及历史记录见 [实施记录](progress.md)，配置见 [本机说明](local-setup.md)。设计中的未完成能力不因代码存在而视为已验收。

建议阅读顺序：

2026-11-02 新增：[企业组织、RBAC 与问答审计改造](enterprise-access-design.md)。本次仅管理当前公司，提供自定义角色、多角色成员、组织树、资料角色授权及统一问答审计；验证见 [本轮记录](../testing/v3/enterprise-access-2026-11-02/report.md)。这不替代下文所列生产验收要求。

1. [首版双底座设计与 Dify 核查](dual-backend.md)：共同能力、平台差异、同步及双向切换。
2. [WeKnora 能力核查](weknora-feasibility.md)：已核实的能力、限制及证据。
3. [V3 技术设计](design.md)：系统职责、权限、资料版本、接口、部署和迁移。
4. [部署验证与验收](validation.md)：实际部署后怎样证明方案可用。
5. [分阶段实施计划](../superpowers/plans/2026-10-30-v3-foundation.md)：文件范围、任务依赖和完成条件。

机器可读证据：[WeKnora 源码检查结果](../testing/weknora-v090-source-audit-2026-10-30.json)、[Dify 源码检查结果](../testing/dify-118-source-audit-2026-10-30.json)。分别为 25 项和 16 项源码结构检查，**不是运行时测试通过**。可使用 [WeKnora 检查脚本](verify-weknora-source.mjs) 与 [Dify 检查脚本](verify-dify-source.mjs) 重复检查。

## 设计选择

采用 FastAPI、Vue 3、自有 PostgreSQL 业务库、自有对象存储、独立业务 Worker，以及两套平台适配器。Dify 仅作为知识服务，不将其工作流作为 V3 的核心依赖。沿用当前团队和代码的 Python 技术路线；用户具有 .NET 经验不自动意味着改用 .NET。改用 ASP.NET Core 不影响本文职责划分，但会改变实施文件和复用范围。

首期做企业文档问答，完整覆盖授权、入库、版本、引用、历史、失败恢复和审计。业务数据库问答、联网搜索、自主执行、Wiki 和 GraphRAG 后续独立评估。

两套底座分别完成 G0，再验证 WeKnora → Dify → WeKnora 双向切换。若某套文档范围过滤、隔离、解析或故障恢复不满足要求，禁止启用该底座；不能为了沿用选型而降低权限要求。首版只有“管理员选择目标、补齐索引、验证后切换”，不承诺两套效果完全一致或未经预热的自动故障切换。
