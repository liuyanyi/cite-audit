import { useEffect, useState } from "react";
import { api } from "./api";
import { Modal } from "./Dialogs";
import { Badge, Button, formatDate, str } from "./ui";

export interface CallCost {
  status: "estimated" | "unknown" | "not_applicable";
  amount: number | null;
  currency?: string;
  pricing?: Record<string, unknown>;
}
export interface AuditCall {
  id: number | string;
  task_id?: string | null;
  entry_id?: string | null;
  created_at?: string;
  provider?: string;
  provider_id?: string;
  model?: string;
  model_config_id?: string;
  client_type?: string;
  status?: string;
  attempt?: number;
  latency_ms?: number;
  external_call?: boolean;
  cache_hit?: boolean;
  query?: unknown;
  messages?: unknown;
  response?: unknown;
  error?: unknown;
  usage_metadata?: Record<string, unknown>;
  tokens?: Record<string, unknown>;
  cost?: CallCost;
  [key: string]: unknown;
}
interface AuditResponse {
  calls: AuditCall[];
  total: number;
  summary: {
    estimated_costs: Array<{ currency: string; amount: number }>;
    unknown_cost_calls: number;
    total_calls: number;
  };
}
export function formatCost(cost?: CallCost) {
  if (cost?.status === "not_applicable") return "不适用";
  if (
    cost?.status !== "estimated" ||
    typeof cost.amount !== "number" ||
    !Number.isFinite(cost.amount)
  )
    return "未知";
  return `${cost.currency || "未知币种"} ${cost.amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 8 })}`;
}
const limit = 50;
export function AuditDialog({
  taskId,
  onClose,
}: {
  taskId?: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<AuditResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [provider, setProvider] = useState("");
  const [filter, setFilter] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setData(null);
    const query = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    if (taskId) query.set("task_id", taskId);
    if (filter) query.set("provider_id", filter);
    api<AuditResponse>(`/api/audit?${query}`)
      .then((result) => {
        if (active) setData(result);
      })
      .catch((failure) => {
        if (active)
          setError(
            failure instanceof Error ? failure.message : "无法加载调用记录",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [taskId, offset, filter, refresh]);
  return (
    <Modal
      id="audit-dialog"
      title={taskId ? "本次检查 · 调用审计" : "调用审计"}
      className="audit-dialog"
      onClose={onClose}
    >
      <div className="audit-content">
        <div className="audit-toolbar">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setOffset(0);
              setFilter(provider.trim());
            }}
          >
            <label>
              Provider ID
              <input
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
                placeholder="全部 provider"
                autoComplete="off"
              />
            </label>
            <Button type="submit" className="small" disabled={loading}>
              筛选
            </Button>
          </form>
          <Button
            className="small"
            icon="refresh"
            disabled={loading}
            onClick={() => setRefresh((value) => value + 1)}
          >
            刷新
          </Button>
        </div>
        {loading && <p role="status">加载调用记录中…</p>}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        {data && (
          <>
            <div className="audit-summary">
              <span>{data.summary.total_calls} 次调用</span>
              <div>
                <span className="subtle">估算成本</span>
                <strong>
                  {data.summary.estimated_costs.length
                    ? data.summary.estimated_costs
                        .map((item) =>
                          formatCost({ status: "estimated", ...item }),
                        )
                        .join(" + ")
                    : "无已知金额"}
                </strong>
              </div>
              {data.summary.unknown_cost_calls > 0 && (
                <Badge tone="warning">
                  {data.summary.unknown_cost_calls} 次费用未知
                </Badge>
              )}
            </div>
            {!data.calls.length && (
              <p className="notice">暂无符合条件的调用记录。</p>
            )}
            <div className="audit-calls">
              {data.calls.map((call) => (
                <details key={call.id} className="audit-call">
                  <summary>
                    <span className="audit-call-title">
                      <strong>{call.model || call.provider || "调用"}</strong>
                      <span className="subtle">
                        {call.provider_id || call.client_type || ""}
                      </span>
                    </span>
                    <Badge
                      tone={
                        call.status === "ok" || call.status === "success"
                          ? "good"
                          : ["error", "failed", "cancelled"].includes(
                                call.status || "",
                              )
                            ? "danger"
                            : "neutral"
                      }
                    >
                      {call.status || "未知状态"}
                    </Badge>
                    <span className="audit-call-cost">
                      {formatCost(call.cost)}
                    </span>
                    <span className="subtle">
                      {formatDate(call.created_at, true)}
                    </span>
                  </summary>
                  <div className="audit-call-body">
                    <div className="source-meta">
                      {[
                        call.attempt ? `第 ${call.attempt} 次尝试` : "",
                        typeof call.latency_ms === "number"
                          ? `${call.latency_ms} ms`
                          : "",
                        call.cache_hit ? "缓存命中" : "",
                        call.task_id
                          ? `任务 ${call.task_id}`
                          : "连接测试 / 独立调用",
                        call.entry_id ? `条目 ${call.entry_id}` : "",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                    {call.error != null && (
                      <p className="inline-error">{str(call.error)}</p>
                    )}
                    <dl className="audit-usage">
                      {Object.entries(
                        call.usage_metadata || call.tokens || {},
                      ).map(([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>{str(value)}</dd>
                        </div>
                      ))}
                    </dl>
                    {call.messages != null || call.query != null ? (
                      <section>
                        <h3>输入</h3>
                        <pre className="code-box">
                          {str(call.messages ?? call.query)}
                        </pre>
                      </section>
                    ) : null}
                    {call.response != null && (
                      <section>
                        <h3>输出</h3>
                        <pre className="code-box">{str(call.response)}</pre>
                      </section>
                    )}
                    {call.cost?.pricing && (
                      <details>
                        <summary>当次计价</summary>
                        <pre className="code-box">{str(call.cost.pricing)}</pre>
                      </details>
                    )}
                    <details>
                      <summary>完整审计记录</summary>
                      <pre className="code-box">{str(call)}</pre>
                    </details>
                  </div>
                </details>
              ))}
            </div>
            <div className="audit-pagination">
              <Button
                className="small"
                disabled={loading || offset === 0}
                onClick={() => setOffset((value) => Math.max(0, value - limit))}
              >
                上一页
              </Button>
              <span className="subtle">
                {data.total
                  ? `${offset + 1}–${Math.min(offset + data.calls.length, data.total)} / ${data.total}`
                  : "0 条"}
              </span>
              <Button
                className="small"
                disabled={loading || offset + limit >= data.total}
                onClick={() => setOffset((value) => value + limit)}
              >
                下一页
              </Button>
            </div>
          </>
        )}
      </div>
      <div className="dialog-footer">
        <Button onClick={onClose}>关闭</Button>
      </div>
    </Modal>
  );
}
