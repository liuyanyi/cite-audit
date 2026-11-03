# Cite-Audit · 领域数据与判定契约

版本 1.0 · 2026-10-30 · 全新实现的统一领域语义

本文规定**什么对象可以存、如何归属、何时可以推荐**，供后端、前端、测试共同使用。这是新项目的概念契约，不是历史数据库结构或兼容映射。实现可采用不同的数据库表设计，但不能破坏以下不变量。

## 1. 两个隔离的数据域

| 任务域 / task-scoped | 共享文献知识域 / reusable bibliography knowledge |
| --- | --- |
| 一次 Bib 上传的不可变原文、任务配置和任务状态 | Article、PublicationVersion、SourceContent及其证据 |
| `citation_key`、用户提交的字段与原始顺序 | 来源命名空间下的稳定标识符和出版记录身份 |
| 本次字段核验结论、纠错提案、推荐展示 | 有来源依据的同版本整合结果与推荐指针 |
| 作者本次导出选择、导出文件、变更清单 | 版本归属、同版本来源聚合、推荐规则修订 |

**严禁**将某次作者选择（采纳、忽略、保留原版）、旧任务“核验通过”或未经来源确认的 Bib 字段保存为共享事实。再次上传只是新任务，允许使用同一个知识库，却要重新核验当前输入。

## 2. 核心对象与建议字段

| 对象 | 稳定身份 | 核心内容 | 必须维护的关联 |
| --- | --- | --- | --- |
| `Article` | 独立 `article_id`，不以题名为主键 | 文章身份描述、已知版本集合、`recommended_version_id?`、推荐理由及规则修订 | 1:N `PublicationVersion` |
| `PublicationVersion` | 独立 `version_id` | 出版记录类型、规范化身份线索、整合书目字段、逐字段证据和冲突、合并修订 | N:1 `Article`，1:N 来源归属 |
| `SourceContent` | 独立 `source_id`，含来源命名空间及原始记录 ID | 原始字段、来源 URL、类型、获取/复核状态、快照指针、提取方法 | 允许待归属；已确认归属到正确 Version |
| `SourceSnapshot` | `snapshot_id` / 内容哈希 | 不可变原始响应、响应元信息和采集时间 | 可被多个提取片段引用，不因后续更新覆盖 |
| `FieldSelection` | `version_id + field + merge_revision` | 采用值、规范化值、字段含义、依据 SourceContent、冲突状态 | 可以从原始 SourceContent 重新计算 |
| `VersionRelation` | 显式关系 ID | `preprint_of`、`published_as`、`same_work_version` 等有方向关系及证据 | 不把 `cites`、`extends` 等当可替代证据 |
| `Recommendation` | Article 的当前有效推荐修订 | 推荐版本 ID、理由、依据、规则版本、有效性 | 推荐指向本 Article 的已确认 Version，允许为空 |
| `AuditTask` / `TaskEntry` | `task_id` / `entry_id` | 本次文件、输入条目、当前版本判断、字段结果、任务证据引用 | 与知识库只有带修订/快照的只读证据链接 |

**“文章—版本—源内容”是三个主要业务层次**；额外辅助表用于保存证据与历史，不应被展示为第四种“论文版本”。

## 3. 来源记录与版本归属

对每条新 SourceContent，先保留原始快照，再执行 `candidate → confirmed / rejected / unresolved` 的归属判断。记录需要包含来源自身 ID、命名空间和对象类型（论文、集合、机构仓储记录等）。单独的标题相似或一个聚合来源的 Work ID 不足以证明多个源内容是同一**出版版本**。

**同版本归并支持依据**（需与标题、作者等合理一致）：经过核验的版本 DOI、官方 Anthology ID/论文集 ID、同一出版平台的记录 ID；无强标识符时使用具体卷册、出版场所、年份、页码/文章号和作者顺序共同支撑。DOI 不可无条件信任输入 Bib 自报值。

**仅作候选**：近似题名、语义相关、作者高度重合、搜索引擎同一结果聚合、来自第三方页面的“可能发表于”文字。

**明确区分**：预印本与正式会议版；投稿与已录用论文；会议主会/Findings/Workshop；会议短文与内容明显扩展的后续期刊文章。强行关联时要给出可替代关系的证据，不允许 A≈B、B≈C 就自动合并 A/B/C。

原始来源记录如果同时包含多个发表对象，应建立多个带字段范围的提取片段或保持待归属；不允许把整个聚合记录原封复制到不同版本，造成跨版本字段污染。

## 4. 同版本 Merge 规则

| 输入差异 | Merge 结果 | 必须保留 |
| --- | --- | --- |
| 大小写、标点、BibTeX 转义、会议别名 | 规范化用于比较，原始文本不丢失 | 所有源值、规范化理由 |
| 一方缺失页码/DOI，另一方有充分同版本依据 | 补全版本层字段 | 采用来源、字段位置、依据版本 |
| 已确认同一发表记录，来源字段实质冲突 | 同 Version 内未决或有依据选择，不拆 Version | 冲突的每个值、来源、选择理由 |
| 作者姓名缩写/截断不同 | 区分完整/缩写/截断，不拼合“作者集合” | 顺序与各自完整列表 |
| `first_online_year` 与 `publication_year` 不同 | 以不同语义字段保存，不当作相互矛盾的同一字段 | 日期类型、原始值 |
| 来源抓取内容更新 | 创建新的 SourceSnapshot / merge revision，不建新出版版本 | 旧快照可追溯性 |

`PublicationVersion.integrated_fields` 每个重要字段可标记 `selected / conflicting / unresolved / not_applicable`。`selected` 并不意味着“多个来源投票一致”，而是依据该字段与出版版本匹配的可靠来源。有争议字段不为了生成完整 Bib 而填造。

## 5. 推荐版本契约

文章通常维护零个或一个推荐版本。只有**文章身份与版本关系确认**、版本元数据足以支持完整引用、来源状态未暴露关键风险时才可推荐。默认优先可靠的正式出版记录，但不能简单采用最新年份、最多来源或“期刊 > 会议”的固定序列。明显扩展的新研究可作为相关 Article，不自动成为原 Article 的替代版本。

推荐由规则生成，保存理由与依据；任何新推荐都必须引用已经存在的 `version_id`，不能凭空建立“推荐综合版”。补充同版本字段一般只更新整合结果，不应导致推荐反复跳动。推荐可为空，并向前端解释为什么不能给出可靠建议。

知识库的推荐独立于作者：某次导出选择使用 arXiv，**不会**改变 Article 的默认推荐；作者也可以保持原预印本不改。

## 6. 当前引用的判定与建议

对每个 `TaskEntry` 至少保存以下**独立维度**：

| 维度 | 推荐枚举 / 语义 |
| --- | --- |
| `execution_status` | `queued / running / completed / partial / failed / cancelled` |
| `identity_state` | `confirmed / ambiguous / not_found / insufficient / source_failed` |
| `field_judgment`（逐字段） | `consistent / conflict / completion / normalization / unverifiable / not_applicable` |
| `version_status` | `current_version_confirmed / candidate / unknown` |
| `recommendation_status` | `same_version / upgrade_available / unavailable / ambiguous` |
| `proposal_type` | `same_version_patch / whole_version_switch` |

这些枚举是**新契约建议**，非历史状态继承；可以在实现前统一微调，但不能压成一个 `ok/warning/error` 值。

**例子**：引用 2018 年 BERT 预印本且字段准确，Article 推荐的是 2019 年 NAACL 正式版。当前版本字段判定应为 `consistent`，推荐状态 `upgrade_available`；“year 不同、DOI 不同”仅在 `whole_version_switch` 的差异表出现，不应作为当前版本的 `conflict`。

## 7. 证据链、时效与历史报告

一条事实性修正至少要能追踪 `entry_id → assessment/proposal → version field → source_id → snapshot_id → source URL/record ID`。整合规则需支持重算，而不是拿上次合并结果做新来源“再投票”。

- 查询缓存：围绕 Provider/参数/接口/适配器规则，负结果 TTL 较短；请求错误不写负事实。
- 来源知识：可跨任务复用，但需保留何时检索/复核及其覆盖范围；证据过期不等于事实错误。
- 历史报告：固定当时 `snapshot_id / merge_revision / recommendation_revision`，之后的知识库更新不修改历史结论。
- Mock：隔离命名空间，始终显式标识；不得参与真实知识库的推荐或证据聚合。
- 手工上传的题录：作为作者提供的候选线索，经过来源核验后才可晋升为事实。

## 8. 必须用自动化测试固定的不变量

1. Source A/B 只有大小写差异时，不出现两个 PublicationVersion。
2. Source A 缺页码、B 有页码且是同一版本时，整合页码可追踪到 B。
3. 同一版本 A/B 页码冲突，不因此拆 Version，不自动多数投票。
4. arXiv 与 ACL 正式版属于同一 Article 下**两个** PublicationVersion。
5. 从 V1 切换 V2 的 Bib 不保留 V1 的 DOI/错误 `eprint` 语义。
6. Article 推荐版本一定属于该 Article；推荐为空允许且可解释。
7. 单次作者选择不能改变 Article/Version/SourceContent 的事实记录。
8. 相同 Bib 再上传建立新任务，不继承旧任务状态或通过结论。
9. 无法确认的 DOI、候选、失败、未命中均不得以“真实已核实”入库。
10. 旧历史报告引用的 SourceSnapshot 不随知识刷新被修改。

本文与开发说明共同构成实现契约；如二者表述冲突，以“不可混版本、来源可追溯、任务决策隔离”三个不变量为首要约束。
