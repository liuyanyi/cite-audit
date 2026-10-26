import { Fragment, useEffect, useId, useRef, useState } from "react";
import {
  Badge,
  Button,
  Icon,
  SafeLink,
  copyText,
  formatDate,
  issueText,
  str,
} from "./ui";

export interface Evidence {
  id: string | number;
  provider?: string;
  fields?: Record<string, unknown>;
  method?: string;
  provenance?: unknown;
  url?: string;
  retrieved_at?: string | number;
  expires_at?: string;
  version?: string | number;
  cache_hit?: boolean;
  cached?: boolean;
  cache_state?: string;
  last_checked_at?: string;
  revalidation_error?: string;
  raw?: unknown;
}
export interface Assessment {
  field: string;
  original?: unknown;
  suggested?: unknown;
  judgment?: string;
  evidence_ids?: Array<string | number>;
  reason?: string;
}
export interface Candidate {
  id?: string | number;
  version?: string;
  fields?: Record<string, unknown>;
  title?: string;
  author?: string;
  year?: string | number;
  journal?: string;
  booktitle?: string;
  reason?: string;
  provider?: string;
  url?: string;
  evidence_ids?: Array<string | number>;
}
export interface QueryCall {
  provider?: string;
  source?: string;
  operation?: string;
  method?: string;
  status?: string | number;
  outcome?: string;
  query?: unknown;
  params?: unknown;
  message?: string;
  error?: unknown;
  created_at?: string | number;
  at?: string | number;
  retrieved_at?: string | number;
  latency_ms?: number;
  cache_hit?: boolean;
  cached?: boolean;
  tokens?: number | Record<string, number | undefined> | null;
}
export interface AuditResult {
  identity_state?: string;
  field_state?: string;
  doi_state?: string;
  publication_state?: string;
  evidence?: Evidence[];
  evidences?: Evidence[];
  assessments?: Assessment[];
  issues?: unknown[];
  suggested_bibtex?: string;
  publication_suggestion?: {
    suggested_bibtex?: string;
    warning?: string;
    fields?: Record<string, unknown>;
    evidence_ids?: Array<string | number>;
    relation_evidence_ids?: Array<string | number>;
    original_arxiv_id?: string;
    entry_type?: string;
  };
  candidates?: Candidate[];
  model_suggestions?: Array<{
    field?: string;
    value?: unknown;
    reason?: string;
    quotation?: string;
    evidence_ids?: Array<string | number>;
  }>;
  expires_at?: string;
  checked_at?: string | number;
  cache_source?: string;
  cache_hit?: boolean;
  calls?: QueryCall[];
  query_traces?: QueryCall[];
}
export interface Entry {
  id: string;
  citation_key: string;
  entry_type: string;
  fields: Record<string, string>;
  raw?: string;
  status: string;
  result?: AuditResult | null;
  error?: unknown;
  issues?: unknown[];
  fingerprint?: string;
  is_chinese?: boolean;
  reused?: boolean;
  cache_hit?: boolean;
}

type Tab = "comparison" | "evidence" | "original" | "queries";
type Notify = (message: string, error?: boolean) => void;
interface Props {
  entry: Entry;
  mode: "live" | "mock";
  onManual: () => void;
  onRefresh: () => void;
  notify: Notify;
  initialTab?: Tab;
  taskRunning?: boolean;
}
const stateLabels: Record<string, Record<string, string>> = {
  identity: {
    unique: "唯一确定",
    multiple: "多个候选",
    not_found: "未找到",
    insufficient: "证据不足",
  },
  field: {
    consistent: "一致",
    conflict: "存在冲突",
    incomplete: "可补全",
    partial: "部分未验证",
  },
  doi: {
    verified: "已核验",
    unverified: "未核验",
    missing: "缺失待查",
    conflict: "归属冲突",
    not_found: "未找到",
    failed: "查询失败",
  },
  publication: {
    published: "已是正式版",
    candidate: "正式版候选",
    not_found: "未发现正式版",
    unclear: "关系不明",
  },
};
const judgments: Record<string, string> = {
  consistent: "一致",
  correction: "修正",
  completion: "补全",
  normalization: "规范化",
  upgrade: "版本升级",
  unverified: "未核验",
  not_applicable: "不适用",
};
const runningStates = new Set([
  "queued",
  "pending",
  "running",
  "processing",
  "querying",
  "cancelling",
]);

function StateBadge({ kind, value = "" }: { kind: string; value?: string }) {
  const tone = ["unique", "consistent", "verified", "published"].includes(value)
    ? "good"
    : value === "conflict"
      ? "danger"
      : ["candidate", "incomplete"].includes(value)
        ? "primary"
        : "neutral";
  return <Badge tone={tone}>{stateLabels[kind]?.[value] || "待核验"}</Badge>;
}

export function tokenLabel(usage: QueryCall["tokens"]): string {
  if (usage == null) return "";
  let count: number | undefined;
  if (typeof usage === "number") count = usage;
  else if (usage.total_tokens !== undefined) count = usage.total_tokens;
  else {
    const input = usage.input_tokens ?? usage.prompt_tokens;
    const output = usage.output_tokens ?? usage.completion_tokens;
    if (input === undefined && output === undefined) return "";
    count = Number(input || 0) + Number(output || 0);
  }
  return Number.isFinite(count)
    ? Number(count).toLocaleString() + " tokens"
    : "";
}

export function EntryDetail({
  entry,
  mode,
  onManual,
  onRefresh,
  notify,
  initialTab = "comparison",
  taskRunning = false,
}: Props) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [highlight, setHighlight] = useState<string | null>(null);
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  const evidenceRefs = useRef(new Map<string, HTMLElement>());
  const instanceId = useId();
  const result = entry.result || {};
  const evidence = result.evidence || result.evidences || [];
  useEffect(() => {
    setTab(initialTab);
    setHighlight(null);
  }, [entry.id, initialTab]);
  useEffect(() => {
    if (tab === "evidence" && highlight !== null) {
      evidenceRefs.current
        .get(highlight)
        ?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    }
  }, [tab, highlight]);
  async function copy(value: unknown) {
    const text = str(value);
    if (!text) return;
    try {
      await copyText(text);
      notify("已复制");
    } catch {
      notify("复制失败，请选中文本后手动复制。", true);
    }
  }
  function References({ ids = [] }: { ids?: Array<string | number> }) {
    return (
      <div className="field-evidence">
        {ids.map((id, position) => {
          const index = evidence.findIndex(
            (record) => String(record.id) === String(id),
          );
          return (
            <button
              type="button"
              className="evidence-ref"
              key={`${id}-${position}`}
              disabled={index < 0}
              title={
                index < 0
                  ? "证据记录不可用"
                  : `${evidence[index].provider || "来源"} · ${id}`
              }
              onClick={() => {
                setHighlight(String(id));
                setTab("evidence");
              }}
            >
              {index < 0 ? "证据缺失" : `[${index + 1}]`}
            </button>
          );
        })}
      </div>
    );
  }
  function FieldValue({
    value,
    className = "",
    missing = "未提供",
  }: {
    value: unknown;
    className?: string;
    missing?: string;
  }) {
    const text = str(value);
    return (
      <div className={`value-row ${className}`}>
        <span className={`field-value${text ? "" : " empty-value"}`}>
          {text || (value === "" ? "空值" : missing)}
        </span>
        {text && (
          <button
            type="button"
            className="icon-button copy-value"
            aria-label="复制字段值"
            title="复制字段值"
            onClick={() => void copy(text)}
          >
            <Icon name="copy" />
          </button>
        )}
      </div>
    );
  }
  function CandidateCard({
    candidate,
    published = false,
  }: {
    candidate: Candidate;
    published?: boolean;
  }) {
    const fields = candidate.fields || candidate;
    return (
      <div className="candidate-card">
        <p>{str(fields.title) || "候选记录"}</p>
        <div className="source-meta">
          {[
            fields.author,
            fields.year,
            fields.journal || fields.booktitle,
            published ? candidate.reason : candidate.provider,
          ]
            .filter(Boolean)
            .map(str)
            .join(" · ")}
        </div>
        {published && candidate.url && (
          <SafeLink url={candidate.url} className="text-link">
            查看候选来源
          </SafeLink>
        )}
        <References
          ids={
            candidate.evidence_ids ||
            (candidate.id !== undefined ? [candidate.id] : [])
          }
        />
      </div>
    );
  }
  const tabs: Array<{ id: Tab; title: string; count?: number }> = [
    { id: "comparison", title: "字段对照" },
    { id: "evidence", title: "证据", count: evidence.length },
    { id: "original", title: "原始条目" },
    { id: "queries", title: "查询记录" },
  ];
  const assessments = result.assessments || [];
  const publication = result.publication_suggestion;
  const issues = [
    ...new Set(
      [...(entry.issues || []), ...(result.issues || [])].map(issueText),
    ),
  ];
  const calls = result.calls || result.query_traces || [];
  return (
    <>
      <header className="detail-header">
        <div className="detail-overline">
          <span className="detail-key">
            @{entry.entry_type || "misc"} · {entry.citation_key || "无 key"}
          </span>
          <div className="detail-tools">
            <Button className="small" icon="plus" onClick={onManual}>
              补充题录
            </Button>
            <Button className="small" icon="refresh" onClick={onRefresh}>
              刷新条目
            </Button>
          </div>
        </div>
        <h2 className="detail-title">
          {str(entry.fields?.title) || "缺少标题"}
        </h2>
        <p className="detail-authors">
          {str(entry.fields?.author) || "作者未提供"}
        </p>
        <div className="state-grid">
          {(["identity", "field", "doi", "publication"] as const).map(
            (kind, index) => (
              <div className="state-item" key={kind}>
                <span>{["身份", "字段", "DOI", "版本"][index]}</span>
                <StateBadge kind={kind} value={result[`${kind}_state`]} />
              </div>
            ),
          )}
        </div>
      </header>
      <div className="detail-tabs" role="tablist" aria-label="详情视图">
        {tabs.map(({ id, title, count }, index) => (
          <button
            key={id}
            ref={(node) => {
              tabRefs.current[id] = node;
            }}
            type="button"
            role="tab"
            id={`${instanceId}-${id}`}
            aria-selected={tab === id}
            aria-controls={`${instanceId}-panel`}
            tabIndex={tab === id ? 0 : -1}
            className={`detail-tab${tab === id ? " active" : ""}`}
            onClick={() => setTab(id)}
            onKeyDown={(event) => {
              if (
                !["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)
              )
                return;
              event.preventDefault();
              const next =
                tabs[
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? tabs.length - 1
                      : (index +
                          (event.key === "ArrowRight" ? 1 : -1) +
                          tabs.length) %
                        tabs.length
                ].id;
              setTab(next);
              tabRefs.current[next]?.focus();
            }}
          >
            {title}
            {count !== undefined && <span>{count}</span>}
          </button>
        ))}
      </div>
      <div
        className="detail-body"
        id={`${instanceId}-panel`}
        role="tabpanel"
        aria-labelledby={`${instanceId}-${tab}`}
      >
        {!!entry.error && (
          <div className="banner error">{issueText(entry.error)}</div>
        )}
        {tab === "comparison" && (
          <>
            <div className="section-heading">
              <h3>当前版本</h3>
              {result.suggested_bibtex && (
                <Button
                  className="small"
                  icon="copy"
                  onClick={() => void copy(result.suggested_bibtex)}
                >
                  复制建议 BibTeX
                </Button>
              )}
            </div>
            {assessments.length > 0 ? (
              <table className="field-table">
                <thead>
                  <tr>
                    {["字段", "原值", "建议值", "判断 / 证据"].map((label) => (
                      <th scope="col" key={label}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {assessments
                    .filter((item) => item.judgment !== "upgrade")
                    .map((item, index) => {
                      const judgment = item.judgment || "unverified";
                      const changed = [
                        "correction",
                        "completion",
                        "normalization",
                      ].includes(judgment);
                      const tone =
                        judgment === "consistent"
                          ? "good"
                          : judgment === "correction"
                            ? "warning"
                            : ["completion", "normalization"].includes(judgment)
                              ? "primary"
                              : "neutral";
                      return (
                        <tr key={`${item.field}-${index}`}>
                          <td className="field-name">{item.field}</td>
                          <td>
                            <FieldValue
                              value={item.original}
                              className={`old-value${changed ? " changed" : ""}`}
                            />
                          </td>
                          <td>
                            <FieldValue
                              value={item.suggested}
                              className={changed ? "suggested-value" : ""}
                              missing="无建议"
                            />
                          </td>
                          <td>
                            <Badge tone={tone}>
                              {judgments[judgment] || judgment}
                            </Badge>
                            <References ids={item.evidence_ids} />
                            {item.reason && (
                              <span className="assessment-reason">
                                {item.reason}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            ) : (
              <p className="notice">
                {runningStates.has(entry.status) ||
                (taskRunning && !entry.result)
                  ? "正在获取证据，字段结果将逐条更新。"
                  : "尚无字段级核验结果。缺少证据不代表原值有误。"}
              </p>
            )}
            {issues.length > 0 && (
              <ul className="issues-list">
                {issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            )}
            {!!entry.fields?.doi && (
              <div className="cnki-tools">
                <SafeLink
                  url={
                    "https://doi.org/" +
                    encodeURI(
                      str(entry.fields.doi).replace(
                        /^https?:\/\/(?:dx\.)?doi\.org\//i,
                        "",
                      ),
                    )
                  }
                  className="button small"
                >
                  原条目 DOI
                </SafeLink>
              </div>
            )}
            {(publication || result.publication_state === "candidate") && (
              <section className="version-section">
                <div className="section-heading">
                  <h3>正式发表版本</h3>
                  {publication?.suggested_bibtex ? (
                    <Button
                      className="small"
                      icon="copy"
                      onClick={() => void copy(publication.suggested_bibtex)}
                    >
                      复制正式版 BibTeX
                    </Button>
                  ) : (
                    <Badge tone="neutral">保留当前版本</Badge>
                  )}
                </div>
                {publication ? (
                  <>
                    <p className="notice warning">
                      {publication.warning ||
                        "替换前请确认：正文是否依赖预印本特有内容。正式版建议与当前版本分开保存。"}
                    </p>
                    <dl className="source-fields">
                      {Object.entries(publication.fields || {}).map(
                        ([key, value]) => (
                          <Fragment key={key}>
                            <dt>{key}</dt>
                            <dd>
                              <FieldValue value={value} />
                            </dd>
                          </Fragment>
                        ),
                      )}
                    </dl>
                    <References
                      ids={[
                        ...new Set([
                          ...(publication.evidence_ids || []),
                          ...(publication.relation_evidence_ids || []),
                        ]),
                      ]}
                    />
                    {publication.original_arxiv_id && (
                      <p className="source-meta">
                        原 arXiv：{publication.original_arxiv_id} · 正式版类型：
                        {publication.entry_type || "未提供"}
                      </p>
                    )}
                    {publication.suggested_bibtex && (
                      <details>
                        <summary className="text-link">查看正式版条目</summary>
                        <pre className="code-box">
                          {publication.suggested_bibtex}
                        </pre>
                      </details>
                    )}
                  </>
                ) : (
                  <p className="notice">
                    候选不等于可替换版本，仍需核对文献身份与版本关系。
                  </p>
                )}
                {(result.candidates || [])
                  .filter((candidate) => candidate.version === "published")
                  .map((candidate, index) => (
                    <CandidateCard
                      key={candidate.id ?? index}
                      candidate={candidate}
                      published
                    />
                  ))}
              </section>
            )}
            {result.identity_state !== "unique" &&
              !!result.candidates?.length && (
                <section className="version-section">
                  <h3>文献候选 · 身份待核对</h3>
                  {result.candidates.map((candidate, index) => (
                    <CandidateCard
                      key={candidate.id ?? index}
                      candidate={candidate}
                    />
                  ))}
                </section>
              )}
            {!!result.model_suggestions?.length && (
              <section className="version-section">
                <h3>模型证据线索 · 待人工核对</h3>
                <p className="notice">
                  以下内容未作为已核验修正，也不自动写入建议条目。
                </p>
                {result.model_suggestions.map((item, index) => (
                  <div className="candidate-card" key={index}>
                    <p>
                      {str(item.field)}：{str(item.value)}
                    </p>
                    {item.reason && (
                      <div className="source-meta">{item.reason}</div>
                    )}
                    {item.quotation && (
                      <div className="source-meta">
                        来源摘录：{item.quotation}
                      </div>
                    )}
                    <References ids={item.evidence_ids} />
                  </div>
                ))}
              </section>
            )}
            {result.suggested_bibtex && (
              <details className="version-section">
                <summary className="text-link">查看当前版本建议 BibTeX</summary>
                <pre className="code-box">{result.suggested_bibtex}</pre>
              </details>
            )}
          </>
        )}
        {tab === "evidence" && (
          <>
            {result.expires_at && (
              <p className="source-meta evidence-check-meta">
                {new Date(result.expires_at).getTime() < Date.now()
                  ? "历史核验结果待复核，可复核此条目。"
                  : "建议复核时间 " + formatDate(result.expires_at, true)}
              </p>
            )}
            {(result.cache_source || result.checked_at) && (
              <p className="source-meta evidence-check-meta">
                {[
                  result.checked_at
                    ? "检查 " + formatDate(result.checked_at, true)
                    : "",
                  (
                    {
                      verification: "复用核验缓存",
                      previous_evidence: "复用历史证据",
                      manual: "人工补充",
                      providers: "来源查询",
                    } as Record<string, string>
                  )[result.cache_source || ""] || "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            )}
            {!evidence.length && (
              <p className="notice">
                暂无可展示的证据。未检索到不等于文献不存在。
              </p>
            )}
            {evidence.map((record, index) => {
              const fields = record.fields || {};
              const expired =
                !!record.expires_at &&
                new Date(record.expires_at).getTime() < Date.now();
              const method =
                record.method || (mode === "mock" ? "mock" : "api");
              const isLead = /lead|snippet|search_result/i.test(
                str(record.provenance),
              );
              return (
                <article
                  key={record.id}
                  data-evidence-id={record.id}
                  className={`source-card${highlight === String(record.id) ? " highlight" : ""}`}
                  ref={(node) => {
                    if (node) evidenceRefs.current.set(String(record.id), node);
                    else evidenceRefs.current.delete(String(record.id));
                  }}
                >
                  <div className="source-top">
                    <div className="source-name">
                      [{index + 1}] {record.provider || "来源"}
                      <Badge
                        tone={
                          method === "mock" || method === "manual"
                            ? "manual"
                            : record.cache_state === "stale"
                              ? "warning"
                              : isLead
                                ? "warning"
                                : "good"
                        }
                      >
                        {method === "mock"
                          ? "模拟证据"
                          : method === "manual"
                            ? "人工 · 未在线核验"
                            : record.cache_state === "stale"
                              ? record.revalidation_error
                                ? "历史证据 · 复核失败"
                                : "历史证据 · 待复核"
                              : isLead
                                ? "检索线索 · 待核验"
                                : "在线获取"}
                      </Badge>
                    </div>
                    {record.url && (
                      <SafeLink url={record.url} className="button small">
                        来源
                      </SafeLink>
                    )}
                  </div>
                  {!!fields.title && (
                    <p className="source-title">{str(fields.title)}</p>
                  )}
                  <div className="source-meta">
                    获取 {formatDate(record.retrieved_at, true)}
                    {record.last_checked_at
                      ? ` · 最近复核 ${formatDate(record.last_checked_at, true)}`
                      : ""}
                    {record.version ? ` · 证据版本 ${record.version}` : ""}
                    {record.expires_at
                      ? " · " +
                        (expired
                          ? "待复核"
                          : "建议复核 " + formatDate(record.expires_at, true))
                      : ""}
                    {record.cache_hit || record.cached ? " · 缓存" : ""}
                  </div>
                  {!!record.provenance && (
                    <div className="source-meta">
                      来源链：{str(record.provenance)}
                    </div>
                  )}
                  <dl className="source-fields">
                    {Object.entries(fields)
                      .filter(([key]) => key !== "title")
                      .map(([key, value]) => (
                        <Fragment key={key}>
                          <dt>{key}</dt>
                          <dd>{str(value) || "—"}</dd>
                        </Fragment>
                      ))}
                  </dl>
                  {!!record.raw && (
                    <details>
                      <summary>原始来源记录</summary>
                      <pre className="code-box">{str(record.raw)}</pre>
                    </details>
                  )}
                </article>
              );
            })}
            <section className="version-section">
              <h3>CNKI 公开网页补充</h3>
              <div className="cnki-tools">
                <SafeLink url="https://www.cnki.net/" className="button small">
                  打开 CNKI
                </SafeLink>
                <Button
                  className="small"
                  icon="copy"
                  onClick={() =>
                    void copy(
                      str(entry.fields?.title) +
                        (entry.fields?.author
                          ? " " + str(entry.fields.author).split(" and ")[0]
                          : "") +
                        " site:cnki.net",
                    )
                  }
                >
                  复制查询条件
                </Button>
                <Button className="small" icon="plus" onClick={onManual}>
                  导入 / 粘贴题录
                </Button>
              </div>
              <p className="notice">
                公开网页检索不代表 CNKI
                总库覆盖。题录或详情链接仅作为独立线索保留。
              </p>
            </section>
          </>
        )}
        {tab === "original" && (
          <>
            <div className="section-heading">
              <h3>上传时的原始快照</h3>
              <Button
                className="small"
                icon="copy"
                onClick={() => void copy(entry.raw)}
              >
                复制原条目
              </Button>
            </div>
            <pre className="code-box">{entry.raw || "无原始文本"}</pre>
          </>
        )}
        {tab === "queries" && (
          <>
            {!calls.length && (
              <p className="notice">
                {mode === "mock"
                  ? "离线演示不发起外部 API 调用。"
                  : "尚无查询记录。"}
              </p>
            )}
            {calls.map((call, index) => {
              const failed = ["failed", "error", 429].includes(
                call.status || "",
              );
              const status =
                call.provider === "cnki" && call.status === "no_results"
                  ? "公开网页检索未命中"
                  : failed && String(call.operation || "").includes("fetch")
                    ? "候选待核验"
                    : (
                        {
                          ok: "成功",
                          no_results: "未找到结果",
                          mock: "模拟",
                          error: "请求失败",
                          deferred: "已退避",
                          cached: "缓存",
                        } as Record<string, string>
                      )[String(call.status)] ||
                      str(call.status || call.outcome || "已记录");
              const timestamp = call.created_at || call.at || call.retrieved_at;
              return (
                <article className="query-item" key={index}>
                  <div className="query-header">
                    <span>
                      {call.provider || call.source || "来源"}
                      {call.operation || call.method
                        ? " / " + (call.operation || call.method)
                        : ""}
                    </span>
                    <Badge tone={failed ? "danger" : "neutral"}>{status}</Badge>
                  </div>
                  <div className="query-text">
                    {str(
                      call.query ||
                        call.params ||
                        call.message ||
                        call.error ||
                        "",
                    )}
                  </div>
                  <div className="source-meta">
                    {[
                      timestamp ? formatDate(timestamp, true) : "",
                      call.latency_ms !== undefined
                        ? call.latency_ms + " ms"
                        : "",
                      call.cache_hit || call.cached ? "缓存命中" : "",
                      tokenLabel(call.tokens),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </article>
              );
            })}
          </>
        )}
      </div>
    </>
  );
}
