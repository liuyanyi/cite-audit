# 模型配置与调用审计

## 边界

MMSP `0.5.6` 是应用内直接调用库，不运行 gateway、Tracer 或外部日志服务。当前仅开放 `openai-chat`、`openai-responses`、`ant-messages`。Google、自动路由、跨 provider fallback 不在此实现中。

provider 负责凭据、协议和端点；模型配置负责模型名、温度、输出上限、超时和重试次数。多个模型可共享 provider。默认模型可在每个新任务覆盖；重试/子任务保留所选配置 ID。旧 `llm` 配置自动迁移为一个 provider / model；旧环境变量仍适用于该兼容配置。删除已被任务引用的模型会使任务显式失败，不会静默换模型。

端点必须是公开 HTTPS，无 URL 凭据；私网、localhost、非标准端口仍被拒绝。OpenAI 官方通常用 `https://api.openai.com/v1`，Anthropic 官方用 `https://api.anthropic.com`。不同 SDK 拼接路径不同，不对任意兼容服务路径进行猜测或重写。

## 调用、安全与重试

MMSP 当前没有公开 SDK transport/retry/JSON 注入接口，因此 `app/llm.py` 使用固定版本的窄子类：继承 MMSP 消息转换/流解析，显式建立 SDK 客户端，设置 SDK `max_retries=0`，并注入已有安全传输。Chat / Responses 请求 JSON object；Anthropic 强制使用仅返回数据的 `submit_audit` 工具，工具不会被执行。所有输出仍经过原有 schema、证据 ID 和事实支持校验。

每个实际 HTTP 尝试由应用计入预算、执行限流、处理 Retry-After 并单独记录。短暂网络/服务错误可有界重试；鉴权错误和无效输出不重试。模型 provider 独立维护冷却时间。传输在后台工作线程内同步完成，取消不会遗留一个脱离所有权的异步请求；当前请求结束后不再启动新请求。

HTTP 客户端使用既有 DNS 固定连接、TLS 校验、公开 IP 校验、禁鉴权 POST 重定向及解压后 2 MiB 上限。缓冲后的 SSE 交给 MMSP 解析；不是实时推送到浏览器。SDK 不使用环境代理，也不从环境中回退选择其他凭据。

## 审计与价格

独立 SQLite 调用账本保留任务/条目/模型配置/provider、请求参数、规范化响应、失败的部分文本或工具参数、usage、完成原因、HTTP 状态、请求 ID、错误及单次耗时。连接测试也入账；删除任务不会级联删除账本。正文可能包含论文内容，应保护本地数据目录。

持久化之前移除所有已配置密钥及 JSON 转义形式。原始 SSE 不落盘，避免跨分片或嵌套转义泄漏凭据。记录规范化结果不等于保存所有原始网络字节；无效/不完整输出仍有失败调用记录。历史已脱敏数据不依赖当前密钥才能安全读取。

使用 `mmsp.list_supported_models(currency="USD")` 的本地内置目录（无外部价格请求），按兼容协议、精确模型名与端点匹配。若匹配项价格冲突则未知；不根据用户起的显示名称推断价格。版本固定意味着目录不是实时价格，升级依赖前应重新验证。每次记录目录版本、币种和四类每百万 token 单价快照。

计算方式：`sum(token_count * price_per_million / 1_000_000)`，使用 cached / prompt / thoughts / response 四桶。MMSP 把未拆分输出全部保留在 response；仅当 thoughts 与 response 同价时可使用该合计。缺失其他 usage/单价、未匹配模型以及目录无法表达的 Anthropic cache-write 费用保持未知，已知零 token 才是零。请求失败仍可能收费，保留能获得的 usage 并估算，不能因失败就计零。

这是模型目录价格估算，不是供应商实际账单；合同折扣、税、检索/抓取额外收费不自动纳入。汇总保持不同币种隔离，并单独显示未知费用调用。离线或缓存命中不产生新远程费用。

## 离线验证与升级

`python -m unittest discover -s tests -v` 使用真实已安装 MMSP + 固定 SDK 和 MockTransport 的协议数据，不使用真实 key、网络、额度。覆盖三协议、结构化输出、显式凭据、重试/预算、重定向、gzip、缺失 usage、截断响应、转义凭据、单价匹配及失败缓存写入。

当前请求始终包含配置的 temperature；不支持该参数的模型尚不能使用（目录中列出并不代表已兼容）。这些检查不能证明任意供应商兼容；真实调用仅由用户主动选择在线任务或连接测试。升级 MMSP / OpenAI / Anthropic / httpx2 后必须重跑上述测试并检查窄子类接口；不可直接移除安全桥来绕过兼容性问题。
