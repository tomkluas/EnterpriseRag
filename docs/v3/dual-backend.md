# V3 首版支持 WeKnora 与 Dify 的设计

## 1. 首版承诺与边界

首版同时实现 WeKnora 和 Dify 两套知识适配器，使用相同的页面、企业身份、资料权限、原件、会话、最终精排和生成逻辑。管理员按业务知识库选择当前底座，目标底座就绪并通过验证后切换。这里的 Dify 指独立的知识库 API，不使用 Dify Chatflow、Workflow 或其聊天记录作为 V3 的核心运行依赖。

“支持切换”意味着两套适配器和往返切换都属于首版验收范围，不只是给未来留一个接口。它不意味着两套平台效果一致、向量可直接复制、故障时任意自动切换，也不意味着所有平台特有功能都互通。

默认模式为“一个底座对外回答，另一个按需建立索引并验证”。不要求长期双写。需要随时快速切换时，由管理员启用影子同步，即同步建立第二套索引但不向用户返回它的回答；只有同步水位一致时才能切换。

## 2. Dify 支持性核查

核查对象：Dify **1.18.0**，官方标签提交 `50abea97062110044f15d0e03ccecadd9ccc87ca`。最初完成 16 项源码检查；随后已运行隔离实例，发现原始镜像文件上传的租户上下文断言错误。固定镜像的最小兼容补丁已通过 7 项上传／元数据／经济索引过滤诊断；高质量语义索引、真实模型和完整 G0 仍未通过。原始与补丁报告分别保留，见 [实施记录](progress.md)。

| 需要的能力 | Dify 固定版本源码证据 | V3 的处理 |
| --- | --- | --- |
| 独立检索 | `POST /v1/datasets/{dataset_id}/retrieve`，返回 records／segment | 自己组织答案，不绑定工作流 |
| 文件上传 | `POST /v1/datasets/{dataset_id}/document/create-by-file`，返回 document 和 batch，200 | 保存文档 ID 与批次 ID，等待完成 |
| 状态 | `GET /v1/datasets/{dataset_id}/documents/{batch}/indexing-status` | 归一化处理中、完成、失败、暂停等状态 |
| 删除 | 文档 DELETE 返回 204，有独立状态／清理链路 | 204 不解析 JSON；业务先撤回，物理清理单独核对 |
| 文档范围 | retrieval_model 的 metadata_filtering_conditions 先解析为 document_ids_filter，再传给召回 | 用 V3 自己写入的不可变绑定标识限制文档 |
| 无匹配范围 | HitTestingService 对有效过滤无命中直接返回空结果 | 适配器仍先阻止空授权，禁止删掉过滤后重试 |
| 数据集凭据范围 | DatasetApiResource 校验 key 的 dataset scope；无绑定可能表示全空间访问 | 必须配置并实测数据集白名单；不能套用 WeKnora 的角色名称 |
| 来源 | 返回 segment.id、document_id、position、content 等 | 可统一文档／片段引用；position 不是原始页码 |
| 精确定位／OCR | 以上检索响应不保证与 WeKnora 相同的原文坐标；OCR 依赖实际解析配置 | 建立能力标志并逐格式验收，不承诺自动等价 |
| 长问题 | HitTestingPayload query 最长 250 字符 | 仅压缩检索问题；保留用户原问，超限不能无声截断 |

源码入口：

- [独立检索路由](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/api/controllers/service_api/dataset/hit_testing.py)、[文件与状态接口](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/api/controllers/service_api/dataset/document.py)。
- [请求模型](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/api/services/knowledge/entities/knowledge_entities.py)、[元数据更新](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/api/controllers/service_api/dataset/metadata.py)。
- [过滤后召回](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/api/services/hit_testing_service.py)、[过滤条件转文档集合](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/api/core/rag/retrieval/dataset_retrieval.py)、[检索器分发](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/api/core/rag/datasource/retrieval_service.py)。
- [凭据校验](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/api/controllers/service_api/wraps.py)、[响应结构](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/api/fields/hit_testing_fields.py)、[查询长度](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/api/controllers/console/datasets/hit_testing_base.py)。

当前只确认过滤参数进入向量和全文检索调用，没有对每一种 Dify 向量数据库逐一验证。Dify G0 使用隔离的标准部署和固定的单一检索存储配置，并分别验收关键词、语义和混合路径；选定存储、扩展、模型插件及 digest 必须记录在部署清单。更换存储后重做相关用例。

[Dify 许可证](https://github.com/langgenius/dify/blob/50abea97062110044f15d0e03ccecadd9ccc87ca/LICENSE) 对多工作空间运营及其前端标识有附加条件。首个企业内部单工作空间部署与以后多客户托管必须分别核对适用条件，不能因使用自己的前端就认定所有条件不适用。

## 3. 统一接口与平台差异

`KnowledgeBackend` 保留 prepare_collection、submit、status、retrieve、delete、capabilities 六个接口。当前 `prepare_collection` 只读验证管理员预建的物理知识库及元数据字段，再返回绑定；机器凭据不自行创建远端库或扩大权限。实现类为 `WeKnoraBackend`、`DifyBackend`，通过 `BackendRegistry.get(backend_instance_id)` 选择。能力不足时阻止发布／切换。

统一范围包含业务允许的文档版本与目标平台绑定，不包含 WeKnora 特有的 knowledge_ids。`Evidence` 增加 backend_instance_id、collection_id 和 locator_level；定位等级为 document、segment、page、region。片段位置不能提升为页码。

WeKnora 适配器将范围转换为 knowledge_ids，并关闭自动上下文扩展。Dify 适配器将范围转换为元数据条件。两者都要对响应的平台文档编号做唯一映射和授权复核，剥离未经授权的附加链接、图片、摘要。

V3 统一精排，适配器关闭平台最终精排，避免切换后回答依赖两套不同排序规则。Dify 的混合检索融合配置仍需显式核对；若配置要求权重，使用该版本合法权重配置，不能把缺省值视为一定可用。可先分别取得语义与全文候选，再由 V3 统一融合；不得省掉某条约定召回路径而仍宣称同一配置。

## 4. Dify 文档级过滤与入库顺序

为每条后端绑定生成唯一 `v3_binding_id`。在 Dify 数据集中创建同名 string 元数据字段，文件入库后通过文档元数据接口写入该值，并读取详情核对实际值。V3 是该字段的唯一写入者，禁止最终用户直接操作平台编辑器或写接口。

入库必须经过：上传原件 → 等待索引完成 → 写入并读取校验元数据 → 检查分块／引用能力 → ready。任何一步失败，保持未发布状态，不得在查询时移除过滤补救。

示例为完整的 V3 适配器检索请求，不是平台已经实测通过的配置。这里用语义路径说明过滤；全文与混合路径分别验证。

```http
POST /v1/datasets/{dataset_id}/retrieve
Authorization: Bearer <服务端数据集凭据>
Content-Type: application/json
```

```json
{
  "query": "上海差旅住宿标准",
  "retrieval_model": {
    "search_method": "semantic_search",
    "reranking_enable": false,
    "top_k": 50,
    "score_threshold_enabled": false,
    "metadata_filtering_conditions": {
      "logical_operator": "and",
      "conditions": [{
        "name": "v3_binding_id",
        "comparison_operator": "in",
        "value": ["allowed-binding-a", "allowed-binding-b"]
      }]
    }
  }
}
```

绑定 ID 指向具体文档版本和索引代次，不使用可能对应旧版的文件名或模糊标签。当前授权范围只列出已发布的绑定。空值、空条件、字段缺失、绑定值重复或接口不支持时直接失败；缺少字段而无命中不等于可发布。

元数据可被误改，G0 除 A/B 反例外，还必须验证重复标识、字段删除、元数据尚未写入、无匹配值及伪造条件。重复绑定标识或未授权结果是完整性错误，整次请求失败。若选定配置无法可靠落实召回范围，禁止启用 Dify，通过物理数据集隔离等方式修正并重新验证；不能只做生成后的过滤。

## 5. 引用与解析的共同能力

首版必须共同支持“文档名称＋版本＋证据片段＋原文访问”。精确页码和区域定位按实际能力分级，WeKnora 定位信息不能硬套到 Dify 的 segment.position。

每个业务知识库配置 `required_capabilities`，例如扫描件要求 OCR，法务资料要求页码定位。目标底座未通过这些能力的逐格式验证时禁止切换。普通资料允许片段引用的前提下可切换；已有更严格承诺的空间不能自动降级。未来可增加独立解析与来源映射服务，但首版不以尚未实现的解析服务掩盖差异。

切换不保证答案逐字一致，只要求权限、版本、引用真实性、拒答及约定质量指标一致达标。两边都使用相同的业务验收题库，另外报告各自解析与定位表现。

## 6. 数据模型与切换流程

补充以下模型：

| 表／字段 | 作用 |
| --- | --- |
| backend_instances | 平台类型、地址、版本、密钥引用、能力验证报告；实例 ID 区别相同平台的多部署 |
| space_backend_routes | 业务知识库、active_backend_instance_id、route_revision、所需能力 |
| backend_bindings | 增加 backend_instance_id、collection_id、remote_document_id、batch_id、v3_binding_id；平台反向映射在实例范围内唯一 |
| backend_replica_states | 每份业务版本在每个底座的就绪状态、配置版本、同步水位 |
| backend_switches | 源／目标、范围清单与指纹、预期路由修订、验收报告、执行状态和审计 |

同一份业务文档可在两套底座各有一条独立绑定；仍禁止同一底座记录归属多个业务文档。检索证据去重键包含底座实例、集合、文档和块，不能只比较两平台的 chunk_id。一次正常查询按业务空间只使用其当前活动底座，不混合两平台结果。

管理员切换流程：

1. 选择目标，检查凭据、已验证能力和版本兼容性。
2. 从自有原件向目标补齐当前及仍需使用的历史版本，等待全部索引和过滤标识就绪。首次导入需要时间，不承诺即时切换。
3. 运行相同问题集及权限反例，确保质量和 required_capabilities 达标。
4. 做最后增量同步；暂停该业务空间的资料发布，记录授权、目录、目标同步水位和路由修订。撤权和删除继续生效，并使过期的切换检查作废。
5. 在短事务内取得与模型派发相同的企业协调键独占锁，核验授权／目录／路由修订、清单未变、所需副本全部 ready；不符则重试同步。事务切换 active_backend_instance_id，递增 route_revision，然后恢复发布。资料发布／撤回也使用该独占锁；生成派发在同键共享锁内复查三类修订后发出请求。不能只检查“平台健康”或只用 expected_route_revision 防管理员并发，而留下“检查后、派发前”窗口。
6. 已开始的查询记录并固定自己的路由快照；未派发生成前发现路由变更则重新检索一次，重复变化则明确失败。已开始输出的请求可在原底座快照上完成，但持续遵守撤权；不会半条回答中途换证据。新查询使用新路由。
7. 回退也走相同预检、同步和事务流程，确保最新新增、更新、撤回与撤权没有丢失。源底座失效则显式失败，不在员工请求中自动回退到未同步底座。

管理员界面至少提供：当前底座、目标同步进度、能力差异、验证结果、切换资格、执行／回退和审计。操作权限与资料阅读权限分离。

## 7. 首版必须执行的双向验收

- 对同一 `KnowledgeBackend` 契约运行两套实现，分别执行全部 G0 用例，不以一套通过替代另一套。
- Dify 专项覆盖 metadata `in` 的真实生效、空匹配、字段删除、缺失／重复值、250 字符限制、200 上传／204 删除、批次状态以及向量存储过滤。
- 相同原件分别入库，验证 WeKnora → Dify 和 Dify → WeKnora；用户、权限、会话、文档编号保持一致，证据依各自真实映射生成。
- 切换准备期间执行新增、新版发布、删除、撤权、Worker 重启和重复请求，验证无旧版本／旧权限复活。
- 目标未就绪、资料数不齐、缺所需定位能力、任一权限反例失败时按钮不可执行，服务端也必须拒绝。
- 每次生成保存路由修订、底座实例及证据快照；切换后旧引用仍经 V3 授权访问自有原件，不依赖已退役平台的直链。

只有两套 G0 和双向切换均通过，才能宣布“首版支持两者切换”。若只有一套通过，可作为单底座试点，但必须明确双底座目标未完成。
