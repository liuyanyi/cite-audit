# cite-audit

本地、单用户的 BibTeX 引文校验工作台。上传 → 对照字段和证据 → 手动修改本地文件 → 增量复查。不会覆盖或自动改写原 `.bib`。

**当前交付：可运行应用 + 离线验收。真实 API、用户额度及真实 CNKI 样本尚待实测。** 默认是显著标识的离线演示，不会悄悄调用在线服务。

UI 原则：**“Avoid unnecessary secondary labels and descriptions. Let the layout do the explaining”.**

## 启动

当前实现仍在 Draft PR #1 的 `feat/evidence-workbench` 分支，尚未合并到 `main`。以下命令明确检出该分支。

Python 3.11+，推荐 3.12。支持 macOS、Linux 和 Windows；Windows 文件锁分支有单元测试，原生 Windows 整体运行仍需实机验证。

```bash
git clone --branch feat/evidence-workbench https://github.com/liuyanyi/cite-audit.git
cd cite-audit
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell 改用：.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m app
```

打开 http://127.0.0.1:8000 。程序只绑定本机地址，不提供公开部署或多用户安全边界。不要使用多个 Uvicorn workers。

上传 `examples/demo.bib`，选择“离线演示”，即可不使用密钥跑完整流程。示例文献、DOI 和模拟证据不能用于真实论文修订。`requirements.lock` 固定全部运行时依赖；`requirements.txt` 列出直接依赖。

也可 `python -m pip install .`，之后从任意目录运行 `python -m app`。React 构建资源及迁移文件包含在 wheel 中。前端源文件修改后应先按下方开发步骤构建，再打包。

## 在线核验

在设置中配置三组服务，或复制 `.env.example` 为启动目录的 `.env`：

- 大模型：兼容 Chat Completions 的 HTTPS Base URL、模型名、API key。要求支持 JSON object 输出；连接测试会检查该能力。
- DeepSeek：有 Search / Fetch 权限的 key。用于公开网页补充检索；不假定普通聊天权限等同于工具权限。
- Semantic Scholar：API key 和实际获批速率，默认 1 次/秒；无 key 时可尝试公开共享配额。

Crossref、DOI 内容协商、DataCite 和 arXiv 是内置公开来源，无需增加 key 组。缺失的可选服务会跳过，不会伪装成在线成功。**保存配置不发请求；点击连接测试会真实联网，可能计费。** 测试成功后，在上传窗口明确选择“在线核验”。任务将只发送所需条目/字段，而非整库。

key 不回传前端，不写入结果或日志。UI 保存的 key 位于本地数据目录的 `settings.json`，**是明文文件，不是加密密钥保险箱**；POSIX 权限为 0600，Windows 应保护该目录的账户访问权限。环境变量优先于 UI 配置。不要把 key 发到聊天、提交 Git 或放入 `.bib`。

[真实 API 验收步骤](docs/live-validation.md)列出了下一步所需权限、样本和停止条件。

## 能做什么

- 有界状态机解析器：宏、拼接、嵌套值、注释、crossref、重复 key/字段和损坏条目恢复；原文件字节及条目原文分别保存。
- 标题、作者、年份、出处、卷期、页码/文章号、DOI 的逐字段证据对照。未知、冲突、补全和规范化分开显示。
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

公开网页未命中不代表 CNKI 未收录。只相似的论文、扩展版、无证据字段不会自动变成修正。单条建议保留 citation key 和非冲突自定义字段；不会导出自动改写的整库。

## 数据与缓存

默认保存在用户主目录的 `.cite-audit/`；可用 `CITE_AUDIT_DATA_DIR` 更改。SQLite 保存原始上传、条目、查询缓存、历史证据、人工记录和调用统计。

- “清除查询缓存”删除可复用查询/核验缓存，历史证据不变。
- 删除任务同时删除该任务上传快照和结果，不影响本地原文件、其他任务或共享缓存。
- 删除人工记录只影响后续复用；历史快照仍需分别删除任务。
- 彻底清理：先停止应用，再备份或删除整个数据目录；这也会删除设置和本地保存的 key。

已发表元数据默认 180 天、搜索 30 天、负结果 7 天。未发现正式版的结果不会被正向 arXiv 元数据延长到 180 天。刷新生成新的任务和证据快照，不篡改历史依据。备份时先停止应用，或使用 SQLite 的一致性备份，不要只复制正在写入的数据库而漏掉 WAL。

## 检查与开发

```bash
python -m unittest discover -s tests -v
python scripts/benchmark_offline.py
python -m pip wheel --no-deps -w dist .
python scripts/check_wheel.py dist
```

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
