# cite-audit

本地、单用户的 BibTeX 引文校验与文献库工作台。上传 → 对照字段和证据 → 选择采纳 → 导出新的完整 Bib → 增量复查。不会覆盖原 `.bib`。

**当前交付：可运行应用 + 离线验收。真实 API、用户额度及真实 CNKI 样本尚待实测。** 默认是显著标识的离线演示，不会悄悄调用在线服务。

UI 原则：**“Avoid unnecessary secondary labels and descriptions. Let the layout do the explaining”.**

## 启动

当前实现仍在 Draft PR #1 的 `feat/evidence-workbench` 分支，尚未合并到 `main`。以下命令明确检出该分支。

使用 [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13.4+ 管理 Python 和依赖。支持 Python 3.11+，项目默认使用 3.12（见 `.python-version`）。支持 macOS、Linux 和 Windows；Windows 文件锁分支有单元测试，原生 Windows 整体运行仍需实机验证。

```bash
git clone --branch feat/evidence-workbench https://github.com/liuyanyi/cite-audit.git
cd cite-audit
uv sync --locked
uv run --locked python -m app
```

打开 http://127.0.0.1:8000 。程序只绑定本机地址，不提供公开部署或多用户安全边界。不要使用多个 Uvicorn workers。

以上命令在 macOS、Linux 和 Windows PowerShell 中相同，不需要手动创建或激活虚拟环境；uv 会管理项目的 `.venv`，并在需要时下载 Python。首次同步需要联网下载依赖。

上传 `examples/demo.bib`，选择“离线演示”，即可不使用密钥跑完整流程。示例文献、DOI 和模拟证据不能用于真实论文修订。`pyproject.toml` 声明直接依赖，`uv.lock` 固定跨平台解析结果和包哈希；不再维护重复的 requirements 文件。`--locked` 会在配置与锁文件不一致时失败，而不是静默改锁。

React 构建资源及迁移文件包含在 wheel 中。前端源文件修改后应先按下方开发步骤构建，再打包。

## 在线核验

在设置中配置三组服务，或复制 `.env.example` 为启动目录的 `.env`：

- 大模型：多个 provider 分别配置名称、协议、公开 HTTPS Base URL 和 API key；模型配置选择 provider、模型名及参数，可指定默认模型或为单个任务选择。MMSP 直接调用，支持 OpenAI Chat / Responses 和 Anthropic Messages；不需要 gateway。Chat / Responses 通常填 `https://api.openai.com/v1`，Anthropic 官方填 `https://api.anthropic.com`（不加 `/v1`）。兼容端点仍需通过连接测试。
- DeepSeek：有 Search / Fetch 权限的 key。用于公开网页补充检索；不假定普通聊天权限等同于工具权限。
- Semantic Scholar：API key 和实际获批速率，默认 1 次/秒；无 key 时可尝试公开共享配额。

Crossref、DOI 内容协商、DataCite、arXiv 和已知标识符的 ACL Anthology 是内置公开来源，无需增加 key 组。缺失的可选服务会跳过，不会伪装成在线成功。**保存配置不发请求；点击连接测试会真实联网，可能计费。** 测试成功后，在上传窗口明确选择“在线核验”。任务将只发送所需条目/字段，而非整库。

key 不回传前端，不写入结果或日志。UI 保存的 key 位于本地数据目录的 `settings.json`，**是明文文件，不是加密密钥保险箱**；POSIX 权限为 0600，Windows 应保护该目录的账户访问权限。环境变量优先于 UI 配置。不要把 key 发到聊天、提交 Git 或放入 `.bib`。

“调用审计”展示跨任务及连接测试的逐次请求、规范化响应、错误、usage 和费用估算；删除任务不删除该账本。费用使用固定版本 MMSP 的内置价格目录，不是实时账单；保存每次采用的单价快照。端点/协议/模型不匹配、缺 usage 或不可表示的缓存写入费用会标为未知，绝不当作免费。汇总按币种分组；检索服务的额外收费可能未知。详见 [模型与审计](docs/model-audit.md)。

[真实 API 验收步骤](docs/live-validation.md)列出了下一步所需权限、样本和停止条件。

## 能做什么

- 有界状态机解析器：宏、拼接、嵌套值、注释、crossref、重复 key/字段和损坏条目恢复；原文件字节及条目原文分别保存。
- 标题、作者、年份、出处、卷期、页码/文章号、DOI 及 editor、month、address、URL 等扩展字段的证据对照。未知、冲突、补全和规范化分开显示。
- 已知 ACL URL、ACL DOI 或 anthology_id 可直接获取官方题录；核实同一出版记录后修正类型与配套 journal/booktitle，不按网站统一类型，不抓取 Google Scholar。
- 整库差异预览、逐字段采纳与新 Bib/修改清单下载；引用必需字段和丰富信息预设只控制采纳哪些建议，不删除原有字段，也不自动升级发表版本。
- 持久文献库区分未确认候选与已确认身份，保留来源快照和判断记录；预印本与正式版分开。
- 输入 DOI 必须与文献身份交叉核验；从聚合索引发现的 DOI 未取得同文献的登记证据前，不标为已核验。
- arXiv 与正式版独立呈现。仅明确、类型正确的版本关系和已核实的完整出版记录才能生成整体替换建议；references/cites 关系不算正式版。
- DeepSeek 网页和模型输出保留为有引用的待核对线索，不凭模型记忆补字段，不把网页提示当指令。
- 可选 CNKI 公开网页补充检索，支持关闭/仅中文/全部；人工导入单条 BibTeX、明确格式的 RIS、字段文本或详情链接，始终标注人工来源。
- SQLite 持久任务、重启恢复、取消/重试、四类证据/查询复用、同源去重、字段改动后的再比对、CNKI 分支增量检查。
- 单条/整任务请求预算，包含重试和重定向；独立限流、超时和 Retry-After，失败不会被解释成无 DOI。
- 本机 Host/Origin 防护；受控 HTTP(S) 获取、DNS 校验及连接 IP 固定、重定向逐跳校验、响应大小上限和凭据脱敏。

## 使用边界

上传限 2 MiB、1000 条，UTF-8 编码。BibLaTeX `xdata` 会保留并报告，但不展开；不会执行 TeX。手工 RIS 支持范围见[实现说明](docs/architecture.md)。作者变体、年份和版本判定偏保守，可能需要人工确认，不承诺真实文献准确率。

取消会阻止下一个外部请求；当前已发出的请求会等待成功或超时。任务运行或取消后仍有请求进行时，不能改配置。更换配置后的重试会生成新任务，避免新模型结果污染旧配置缓存。

公开网页未命中不代表 CNKI 未收录。只相似的论文、扩展版、无证据字段不会自动变成修正。单条建议保留 citation key 和非冲突自定义字段。整库导出只应用用户选中的证据支持修改，保存成新文件；类型和配套出处字段共同采纳，正式版升级另行确认。重复 key、歧义语法、无法安全保留的表达式和复核失败的历史证据不自动修改。

## 数据与缓存

默认保存在用户主目录的 `.cite-audit/`；可用 `CITE_AUDIT_DATA_DIR` 更改。SQLite 保存原始上传、条目、查询缓存、持久来源快照、文献库、人工记录和独立调用审计。旧数据目录自动按版本迁移；升级前建议停机备份。

- “清除查询缓存”清理查询/判断复用状态，并使已归档响应下次使用时需要复核；不会删除永久来源快照、文献库或审计。
- 删除任务同时删除该任务上传快照和结果；本地原文件、文献库、来源快照及调用审计/费用记录仍保留。审计正文可能包含条目内容。
- 删除人工记录停止后续复用，不追溯抹除任务和文献库中的历史依据。
- 彻底清理：先停止应用，再备份或删除整个数据目录；这也会删除设置和本地保存的 key。

建议复核周期：已发表元数据默认 180 天、搜索 30 天、负结果 7 天。到期不会删除来源内容；下次使用时定向再确认，支持的 GET 来源使用 ETag / Last-Modified，不支持时取回记录并比较内容。未变化的证据可复用相同输入/模型/参数的模型结果；复核失败保留历史证据但不当作新鲜核验或可导出修正。未发现正式版等发现性查询仍有独立周期。任务“按需复核”优先复用新鲜来源；条目的强制刷新仍可重新请求所选来源。每次复核请求都会记入预算和日志，304 也不是零调用。备份时先停止应用，或使用 SQLite 的一致性备份，不要只复制正在写入的数据库而漏掉 WAL。

## 检查与开发

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/benchmark_offline.py
uv build --wheel
uv run --locked python scripts/check_wheel.py dist
```

`uv build` 使用 `pyproject.toml` 中固定版本的构建后端；wheel smoke 使用 uv 安装本地 wheel 到临时目录，不依赖环境中存在 pip，也不访问在线核验服务。CI 固定 uv 版本，在 Ubuntu/Windows 上执行同样的锁定同步和检查。

修改依赖时使用 `uv add` / `uv remove`，或修改 `pyproject.toml` 后运行 `uv lock`；同时提交 `pyproject.toml` 和 `uv.lock`。有意更新某个间接依赖可运行 `uv lock --upgrade-package 包名`，不要手改 `uv.lock`。更新后重新执行上述检查；日常安装和 CI 保持 `--locked`。

前端使用 React + TypeScript + Vite，源码位于 `frontend/src/`；构建产物随 Python 包分发。日常启动已构建版本只需 Python，不需要 Node。修改前端时需要 Node 22.12+ 与 npm，先安装依赖、运行检查并构建：

```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run build
```

`frontend/src/` 是前端唯一源码入口，不手工修改 `app/static/` 的生成文件。构建会更新这些产物，随后回到仓库根目录运行 Python 检查与打包，并一起提交源码、lockfile 与构建产物。开发时可在后端正常运行的同时执行 `npm run dev`，通过 Vite 代理访问本机 API；不需要放宽后端的 Host/Origin 或 CORS 安全限制。

前端单元与组件交互测试使用 Vitest、jsdom 和 Testing Library；Python 测试另将真实 mock 服务生成的八类文献交给 React 渲染，避免只测手写 fixture。未安装 Node/前端开发依赖时该桥接测试明确跳过，CI 中必须安装后执行。jsdom 不是浏览器，不代表桌面/移动视觉和真实交互已验收。

详见[架构和支持范围](docs/architecture.md)、[验收记录](docs/validation.md)以及原[实施需求](docs/implementation-plan.md)。
