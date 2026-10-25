import { useEffect, useState } from "react";
import { api } from "./api";
import { Modal } from "./Dialogs";
import { Badge, Button, SafeLink, formatDate, str } from "./ui";

interface LibraryItem {
  id: string;
  identity_key: string;
  status: string;
  mode: string;
  version: string;
  fields: Record<string, unknown>;
  identifiers?: Record<string, unknown>;
  citation_keys?: string[];
  first_seen?: string;
  last_seen?: string;
  snapshot_count?: number;
}
interface SourceSnapshot {
  id: string;
  provider?: string;
  url?: string;
  fields?: Record<string, unknown>;
  raw?: unknown;
  retrieved_at?: string;
  last_checked_at?: string;
  expires_at?: string;
  cache_state?: string;
  [key: string]: unknown;
}
interface LibraryDecision {
  task_id: string;
  entry_id: string;
  created_at?: string;
  identity_state?: string;
  assessments?: unknown;
  source_ids?: string[];
}
interface LibraryDetail extends LibraryItem {
  snapshots: SourceSnapshot[];
  decisions: LibraryDecision[];
}
interface LibraryResponse {
  items: LibraryItem[];
  total: number;
}
const modes: Record<string, string> = { live: "在线来源", mock: "离线演示" };
const versions: Record<string, string> = {
  published: "正式发表版本",
  preprint: "预印本",
  unknown: "版本待确认",
};
const freshness: Record<string, string> = {
  fresh: "可复用",
  stale: "待复核",
  revalidating: "复核中",
  changed: "来源已更新",
  unavailable: "复核失败",
};
const limit = 50;
export function LibraryDialog({
  onClose,
  onOpenTask,
}: {
  onClose: () => void;
  onOpenTask: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [mode, setMode] = useState("all");
  const [status, setStatus] = useState("all");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [list, setList] = useState<LibraryResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<LibraryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setList(null);
    setSelected(null);
    const params = new URLSearchParams({
      q: search,
      mode,
      status,
      limit: String(limit),
      offset: String(offset),
    });
    api<LibraryResponse>(`/api/library?${params}`)
      .then((result) => {
        if (active) {
          setList(result);
          setSelected(result.items[0]?.id || null);
        }
      })
      .catch((failure) => {
        if (active)
          setError(
            failure instanceof Error ? failure.message : "无法加载文献库",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [search, mode, status, offset, refresh]);
  useEffect(() => {
    let active = true;
    setDetail(null);
    setDetailError("");
    if (!selected) {
      setDetailLoading(false);
      return;
    }
    setDetailLoading(true);
    api<LibraryDetail>(`/api/library/${encodeURIComponent(selected)}`)
      .then((result) => {
        if (active) setDetail(result);
      })
      .catch((failure) => {
        if (active)
          setDetailError(
            failure instanceof Error ? failure.message : "无法加载来源快照",
          );
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selected, refresh]);
  return (
    <Modal
      id="library-dialog"
      title="文献库"
      className="library-dialog"
      onClose={onClose}
    >
      <div className="library-content">
        <div className="library-toolbar">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setOffset(0);
              setSearch(query.trim());
            }}
          >
            <label className="search-box">
              <input
                type="search"
                aria-label="搜索文献库"
                placeholder="标题、标识符或 citation key"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <Button className="small" type="submit">
              搜索
            </Button>
          </form>
          <select
            aria-label="来源模式"
            value={mode}
            onChange={(event) => {
              setOffset(0);
              setMode(event.target.value);
            }}
          >
            <option value="all">全部来源</option>
            <option value="live">在线来源</option>
            <option value="mock">离线演示</option>
          </select>
          <select
            aria-label="身份状态"
            value={status}
            onChange={(event) => {
              setOffset(0);
              setStatus(event.target.value);
            }}
          >
            <option value="all">全部身份状态</option>
            <option value="confirmed">已确认身份</option>
            <option value="candidate">候选记录</option>
          </select>
          <Button
            className="small"
            icon="refresh"
            disabled={loading}
            onClick={() => setRefresh((value) => value + 1)}
          >
            刷新
          </Button>
        </div>
        {loading && <p role="status">加载文献库中…</p>}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        {list &&
          (!list.items.length ? (
            <p className="notice">
              没有匹配的文献。检查获取的来源记录会保留在这里。
            </p>
          ) : (
            <div className="library-layout">
              <nav className="library-list" aria-label="库中文献">
                {list.items.map((item) => (
                  <button
                    className={`entry-item${selected === item.id ? " selected" : ""}`}
                    key={item.id}
                    aria-pressed={selected === item.id}
                    onClick={() => setSelected(item.id)}
                  >
                    <span className="entry-title">
                      {str(item.fields.title) || item.identity_key}
                    </span>
                    <span className="entry-key-row">
                      <span className="entry-key">
                        {item.citation_keys?.join(", ") || item.identity_key}
                      </span>
                      <span className="entry-year">
                        {str(item.fields.year)}
                      </span>
                    </span>
                    <span className="entry-flags">
                      <Badge
                        tone={item.status === "confirmed" ? "good" : "warning"}
                      >
                        {item.status === "confirmed"
                          ? "已确认身份"
                          : "候选，未核验"}
                      </Badge>
                      {item.mode === "mock" && (
                        <Badge tone="manual">离线演示</Badge>
                      )}
                      <Badge>
                        {versions[item.version] || item.version || "版本待确认"}
                      </Badge>
                    </span>
                  </button>
                ))}
              </nav>
              <section className="library-detail" aria-label="文献来源详情">
                {detailLoading && <p role="status">加载来源快照中…</p>}
                {detailError && (
                  <p className="inline-error" role="alert">
                    {detailError}
                  </p>
                )}
                {detail && (
                  <>
                    {detail.mode === "mock" && (
                      <div className="banner demo">
                        离线演示数据，不可作为真实文献依据。
                      </div>
                    )}
                    <div className="section-heading">
                      <h3>{str(detail.fields.title) || detail.identity_key}</h3>
                      <Badge
                        tone={
                          detail.status === "confirmed" ? "good" : "warning"
                        }
                      >
                        {detail.status === "confirmed"
                          ? "已确认身份"
                          : "候选，未核验"}
                      </Badge>
                    </div>
                    <p className="source-meta">
                      {modes[detail.mode] || detail.mode} ·{" "}
                      {versions[detail.version] || detail.version} ·{" "}
                      {detail.snapshot_count ?? detail.snapshots.length}{" "}
                      份来源快照
                    </p>
                    <dl className="source-fields">
                      {Object.entries(detail.fields)
                        .filter(([key]) => key !== "title")
                        .map(([key, value]) => (
                          <div className="library-field" key={key}>
                            <dt>{key}</dt>
                            <dd>{str(value) || "—"}</dd>
                          </div>
                        ))}
                    </dl>
                    {detail.identifiers && (
                      <p className="source-meta">
                        {Object.entries(detail.identifiers)
                          .map(([key, value]) => `${key}: ${str(value)}`)
                          .join(" · ")}
                      </p>
                    )}
                    <section className="version-section">
                      <h3>来源快照</h3>
                      {detail.snapshots.length ? (
                        detail.snapshots.map((snapshot, index) => (
                          <article
                            className="source-card"
                            key={`${snapshot.id}-${index}`}
                          >
                            <div className="source-top">
                              <strong>{snapshot.provider || "来源"}</strong>
                              <Badge
                                tone={
                                  snapshot.cache_state === "fresh"
                                    ? "good"
                                    : "neutral"
                                }
                              >
                                {snapshot.cache_state === "stale" &&
                                snapshot.revalidation_error
                                  ? "历史证据 · 复核失败"
                                  : freshness[snapshot.cache_state || ""] ||
                                    snapshot.cache_state ||
                                    "历史快照"}
                              </Badge>
                              {snapshot.url && (
                                <SafeLink url={snapshot.url}>来源</SafeLink>
                              )}
                            </div>
                            <p className="source-meta">
                              获取 {formatDate(snapshot.retrieved_at, true)}
                              {snapshot.last_checked_at
                                ? ` · 最近复核 ${formatDate(snapshot.last_checked_at, true)}`
                                : ""}
                              {snapshot.expires_at
                                ? ` · 建议复核 ${formatDate(snapshot.expires_at, true)}`
                                : ""}
                            </p>
                            <details>
                              <summary>字段与原始记录</summary>
                              <pre className="code-box">{str(snapshot)}</pre>
                            </details>
                          </article>
                        ))
                      ) : (
                        <p className="notice">暂无来源快照。</p>
                      )}
                    </section>
                    <section className="version-section">
                      <h3>核验记录</h3>
                      {detail.decisions.length ? (
                        detail.decisions.map((decision, index) => (
                          <details
                            className="library-decision"
                            key={`${decision.task_id}-${decision.entry_id}-${index}`}
                          >
                            <summary>
                              {formatDate(decision.created_at, true)} ·{" "}
                              {decision.identity_state || "身份待核对"}
                            </summary>
                            <Button
                              className="small"
                              onClick={() => onOpenTask(decision.task_id)}
                            >
                              打开检查
                            </Button>
                            <pre className="code-box">
                              {str({
                                entry_id: decision.entry_id,
                                assessments: decision.assessments,
                                source_ids: decision.source_ids,
                              })}
                            </pre>
                          </details>
                        ))
                      ) : (
                        <p className="notice">尚无确认采用此候选的核验记录。</p>
                      )}
                    </section>
                  </>
                )}
              </section>
            </div>
          ))}
        {list && (
          <div className="audit-pagination">
            <Button
              className="small"
              disabled={loading || offset === 0}
              onClick={() => setOffset((value) => Math.max(0, value - limit))}
            >
              上一页
            </Button>
            <span className="subtle">
              {list.total
                ? `${offset + 1}–${Math.min(offset + list.items.length, list.total)} / ${list.total}`
                : "0 条"}
            </span>
            <Button
              className="small"
              disabled={loading || offset + limit >= list.total}
              onClick={() => setOffset((value) => value + limit)}
            >
              下一页
            </Button>
          </div>
        )}
      </div>
      <div className="dialog-footer">
        <Button onClick={onClose}>关闭</Button>
      </div>
    </Modal>
  );
}
