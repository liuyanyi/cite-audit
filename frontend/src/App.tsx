import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { EntryDetail, type Entry } from "./EntryDetail";
import {
  ConfirmDialog,
  ManualDialog,
  SettingsDialog,
  UploadDialog,
} from "./Dialogs";
import { Badge, Button, Icon, formatDate, issueText, str } from "./ui";

export interface Task {
  id: string;
  filename: string;
  status: string;
  mode: "mock" | "live";
  created_at?: string;
  parent_id?: string;
  entries?: Entry[];
  total?: number;
  completed?: number;
  issues?: number;
  failed?: number;
  cache_hits?: number;
  api_calls?: number;
  parse_issues?: unknown[];
}
const runningStates = new Set([
  "queued",
  "pending",
  "running",
  "processing",
  "querying",
  "cancelling",
]);
export const isRunning = (item?: { status: string } | null) =>
  !!item && runningStates.has(item.status);
export const statusLabels: Record<string, string> = {
  queued: "排队中",
  pending: "排队中",
  running: "核验中",
  processing: "核验中",
  querying: "查询中",
  completed: "已完成",
  done: "已完成",
  complete: "已完成",
  partial: "部分完成",
  completed_with_errors: "部分完成",
  failed: "失败",
  error: "失败",
  cancelled: "已取消",
  canceled: "已取消",
  cancelling: "取消中",
  cached: "已复用",
};
const identityLabels: Record<string, string> = {
  unique: "唯一确定",
  multiple: "多个候选",
  not_found: "未找到",
  insufficient: "证据不足",
};
const doiLabels: Record<string, string> = {
  missing: "缺失待查",
  not_found: "未找到",
  unverified: "未核验",
};
const filters = [
  ["all", "全部文献"],
  ["issues", "存在问题"],
  ["conflict", "字段冲突"],
  ["missing_doi", "DOI 缺失 / 未核验"],
  ["upgrade", "正式版候选"],
  ["insufficient", "证据不足"],
  ["failed", "查询失败"],
  ["chinese", "中文文献"],
];
export function matchesEntry(
  entry: Entry,
  query = "",
  filter = "all",
): boolean {
  const result = entry.result || {},
    fields = entry.fields || {};
  if (
    query &&
    !`${entry.citation_key} ${str(fields.title)}`
      .toLowerCase()
      .includes(query.toLowerCase().trim())
  )
    return false;
  switch (filter) {
    case "issues":
      return !!(
        entry.issues?.length ||
        result.issues?.length ||
        ["conflict", "incomplete", "partial"].includes(
          result.field_state || "",
        ) ||
        ["conflict", "failed", "unverified"].includes(result.doi_state || "") ||
        ["multiple", "insufficient", "not_found"].includes(
          result.identity_state || "",
        ) ||
        entry.status === "failed"
      );
    case "conflict":
      return (
        result.field_state === "conflict" || result.doi_state === "conflict"
      );
    case "missing_doi":
      return (
        !fields.doi ||
        ["missing", "not_found", "unverified"].includes(result.doi_state || "")
      );
    case "upgrade":
      return result.publication_state === "candidate";
    case "insufficient":
      return (
        ["insufficient", "multiple", "not_found"].includes(
          result.identity_state || "",
        ) ||
        result.field_state === "partial" ||
        result.doi_state === "unverified"
      );
    case "failed":
      return (
        ["failed", "error"].includes(entry.status) ||
        result.doi_state === "failed"
      );
    case "chinese":
      return !!entry.is_chinese;
    default:
      return true;
  }
}
export function EntryBadges({ entry }: { entry: Entry }) {
  const result = entry.result || {},
    flags: [string, string?][] = [];
  if (["failed", "error"].includes(entry.status))
    return <Badge tone="danger">查询失败</Badge>;
  if (!entry.result)
    return (
      <Badge tone={isRunning(entry) ? "primary" : "neutral"}>
        {statusLabels[entry.status] || "待核验"}
      </Badge>
    );
  if (result.doi_state === "conflict") flags.push(["DOI 冲突", "danger"]);
  else if (result.field_state === "conflict")
    flags.push(["字段冲突", "warning"]);
  if (result.publication_state === "candidate")
    flags.push(["正式版候选", "primary"]);
  if (
    ["insufficient", "multiple", "not_found"].includes(
      result.identity_state || "",
    )
  )
    flags.push([identityLabels[result.identity_state!]]);
  else if (result.field_state === "partial") flags.push(["部分未核验"]);
  if (doiLabels[result.doi_state || ""])
    flags.push(["DOI " + doiLabels[result.doi_state!]]);
  if (result.field_state === "incomplete") flags.push(["可补全", "warning"]);
  if (!flags.length)
    flags.push([
      result.field_state === "consistent" ? "字段一致" : "查看结果",
      result.field_state === "consistent" ? "good" : "neutral",
    ]);
  if (entry.reused || entry.cache_hit || result.cache_hit) flags.push(["缓存"]);
  return (
    <>
      {flags.slice(0, 3).map(([label, tone]) => (
        <Badge key={label} tone={tone}>
          {label}
        </Badge>
      ))}
    </>
  );
}
type DialogState =
  | { kind: "upload"; parentTaskId?: string; navigation: number }
  | { kind: "manual"; entryId: string; navigation: number }
  | { kind: "settings" }
  | null;
type Confirmation = {
  title: string;
  description: string;
  label: string;
  resolve: (value: boolean) => void;
};

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]),
    [task, setTask] = useState<Task | null>(null);
  const [selected, setSelected] = useState<string | null>(null),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all");
  const [dialog, setDialog] = useState<DialogState>(null),
    [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [connectionError, setConnectionError] = useState(""),
    [toast, setToast] = useState<{ message: string; error: boolean } | null>(
      null,
    );
  const [busy, setBusy] = useState(false),
    [initialTab, setInitialTab] = useState<"comparison" | "evidence">(
      "comparison",
    );
  const taskRef = useRef(task),
    tasksRef = useRef(tasks),
    selectedRef = useRef(selected),
    mounted = useRef(false);
  const targetId = useRef<string | null>(null),
    requestSequence = useRef(0),
    navigation = useRef(0),
    historySequence = useRef(0);
  const writing = useRef(false),
    confirmationRef = useRef<Confirmation | null>(null),
    toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  taskRef.current = task;
  tasksRef.current = tasks;
  selectedRef.current = selected;
  const notify = useCallback((message: string, error = false) => {
    if (!mounted.current) return;
    clearTimeout(toastTimer.current);
    setToast({ message, error });
    toastTimer.current = setTimeout(() => setToast(null), error ? 6500 : 3200);
  }, []);
  const confirm = useCallback(
    (title: string, description: string, label = "确认"): Promise<boolean> => {
      if (confirmationRef.current) return Promise.resolve(false);
      return new Promise((resolve) => {
        const next = { title, description, label, resolve };
        confirmationRef.current = next;
        setConfirmation(next);
      });
    },
    [],
  );
  function finishConfirmation(value: boolean) {
    const current = confirmationRef.current;
    confirmationRef.current = null;
    setConfirmation(null);
    current?.resolve(value);
  }
  const refreshHistory = useCallback(async () => {
    const sequence = ++historySequence.current;
    const data = await api<{ tasks: Task[] }>("/api/tasks");
    if (mounted.current && sequence === historySequence.current) {
      tasksRef.current = data.tasks;
      setTasks(data.tasks);
    }
    return data.tasks;
  }, []);
  const loadTask = useCallback(async (id: string, reset = false) => {
    targetId.current = id;
    const sequence = ++requestSequence.current;
    try {
      const next = await api<Task>("/api/tasks/" + encodeURIComponent(id));
      if (
        !mounted.current ||
        sequence !== requestSequence.current ||
        targetId.current !== id
      )
        return;
      const previous = taskRef.current?.entries?.find(
        (entry) => entry.id === selectedRef.current,
      );
      const entries = next.entries || [];
      setSelected(
        reset
          ? entries[0]?.id || null
          : entries.find((entry) => entry.id === selectedRef.current)?.id ||
              entries.find(
                (entry) =>
                  previous && entry.fingerprint === previous.fingerprint,
              )?.id ||
              entries[0]?.id ||
              null,
      );
      taskRef.current = next;
      setTask(next);
      setTasks((current) =>
        current.some((item) => item.id === id)
          ? current.map((item) => (item.id === id ? next : item))
          : [next, ...current],
      );
      if (reset) {
        setQuery("");
        setFilter("all");
        setInitialTab("comparison");
      }
      setConnectionError("");
      try {
        localStorage.setItem("cite-audit.task", id);
      } catch {
        /* Private browsing may disable storage. */
      }
    } catch (error) {
      if (
        mounted.current &&
        sequence === requestSequence.current &&
        targetId.current === id
      )
        setConnectionError(
          "无法更新任务：" +
            issueText(error instanceof Error ? error.message : error),
        );
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    let stopped = false,
      timer: ReturnType<typeof setTimeout>,
      polling = false;
    async function bootstrap() {
      try {
        const history = await refreshHistory();
        if (stopped || targetId.current) return;
        let last: string | null = null;
        try {
          last = localStorage.getItem("cite-audit.task");
        } catch {
          /* Optional persistence. */
        }
        const first = history.find((item) => item.id === last) || history[0];
        if (first) await loadTask(first.id, true);
        else setConnectionError("");
      } catch (error) {
        if (!stopped)
          setConnectionError(
            "无法连接本地服务：" +
              issueText(error instanceof Error ? error.message : error),
          );
      }
    }
    async function poll() {
      if (stopped) return;
      if (!polling && !document.hidden) {
        polling = true;
        try {
          if (!targetId.current) await bootstrap();
          else if (
            isRunning(taskRef.current) &&
            targetId.current === taskRef.current?.id
          )
            await loadTask(targetId.current);
          else if (tasksRef.current.some(isRunning)) {
            await refreshHistory();
            if (!stopped && targetId.current) await loadTask(targetId.current);
          }
        } catch {
          /* Keep the previous results visible while disconnected. */
        } finally {
          polling = false;
        }
      }
      if (!stopped) timer = setTimeout(poll, 2000);
    }
    function visible() {
      if (!document.hidden && !polling) {
        clearTimeout(timer);
        void poll();
      }
    }
    void bootstrap().then(() => {
      if (!stopped) timer = setTimeout(poll, 2000);
    });
    document.addEventListener("visibilitychange", visible);
    return () => {
      stopped = true;
      mounted.current = false;
      ++requestSequence.current;
      ++historySequence.current;
      clearTimeout(timer);
      clearTimeout(toastTimer.current);
      document.removeEventListener("visibilitychange", visible);
      confirmationRef.current?.resolve(false);
      confirmationRef.current = null;
    };
  }, [loadTask, refreshHistory]);
  async function reconnect() {
    if (targetId.current) {
      await loadTask(targetId.current);
      return;
    }
    try {
      const history = await refreshHistory();
      if (history[0]) await loadTask(history[0].id, true);
      else setConnectionError("");
    } catch (error) {
      setConnectionError(
        "无法连接本地服务：" +
          issueText(error instanceof Error ? error.message : error),
      );
    }
  }
  function selectTask(id: string) {
    ++navigation.current;
    void loadTask(id, true);
  }
  function openUpload() {
    setDialog({
      kind: "upload",
      parentTaskId: task?.id,
      navigation: navigation.current,
    });
  }
  async function onCreated(
    id: string,
    capturedNavigation: number,
    evidence = false,
  ) {
    setDialog(null);
    if (capturedNavigation === navigation.current) {
      await loadTask(id, false);
      if (evidence) setInitialTab("evidence");
    }
    try {
      await refreshHistory();
    } catch {
      /* Never repeat the completed write. */
    }
    notify(
      evidence
        ? "人工证据已保存，未标记为在线核验。"
        : "检查已创建，原文件保持不变。",
    );
  }
  async function operation(
    path: string,
    message: string,
    body?: unknown,
    remove = false,
    capturedNavigation = navigation.current,
  ) {
    if (writing.current) return;
    writing.current = true;
    setBusy(true);
    const oldId = taskRef.current?.id;
    try {
      const response = await api<{
        id?: string;
        task?: { id: string };
        unchanged?: boolean;
      }>(path, { method: remove ? "DELETE" : "POST", body });
      if (capturedNavigation === navigation.current && mounted.current) {
        if (remove) {
          ++requestSequence.current;
          targetId.current = null;
          taskRef.current = null;
          setTask(null);
          setSelected(null);
          try {
            localStorage.removeItem("cite-audit.task");
          } catch {
            /* Optional persistence. */
          }
        } else {
          const id = response.id || response.task?.id || oldId;
          if (id) await loadTask(id);
        }
      }
      try {
        const history = await refreshHistory();
        if (
          remove &&
          capturedNavigation === navigation.current &&
          !targetId.current &&
          history[0]
        )
          await loadTask(history[0].id, true);
      } catch {
        /* Write already succeeded. */
      }
      notify(response.unchanged ? "没有需要刷新的过期结果。" : message);
    } catch (error) {
      notify(issueText(error instanceof Error ? error.message : error), true);
    } finally {
      writing.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function taskAction(
    action: "cancel" | "retry" | "refresh-stale" | "delete",
  ) {
    if (!task || writing.current) return;
    const captured = task,
      capturedNavigation = navigation.current;
    const descriptions = {
      cancel: [
        "取消当前检查？",
        "已完成的结果和证据将保留。正在执行的请求可能仍会完成。",
        "取消检查",
        "已请求取消",
      ],
      retry: [
        "重试失败条目",
        "仅重试失败或未完成条目，可能调用已配置的在线来源并产生费用。",
        "重试",
        "已创建重试任务",
      ],
      "refresh-stale": [
        "刷新过期结果",
        captured.mode === "live"
          ? "仅复查证据已过期的条目。将重新查询相关在线来源，可能产生费用；历史任务和旧证据保留。"
          : "仅刷新过期的模拟结果，不发起外部请求。历史任务和旧证据保留。",
        "刷新过期结果",
        "已提交过期结果刷新",
      ],
      delete: [
        "删除这次检查？",
        `将删除“${captured.filename}”的任务记录、上传快照及其检查结果，此操作不可撤销。本地原文件和其他任务不受影响。`,
        "删除检查",
        "检查记录已删除",
      ],
    }[action];
    if (
      !(action === "retry" && captured.mode !== "live") &&
      !(await confirm(descriptions[0], descriptions[1], descriptions[2]))
    )
      return;
    await operation(
      "/api/tasks/" +
        encodeURIComponent(captured.id) +
        (action === "delete" ? "" : "/" + action),
      descriptions[3],
      undefined,
      action === "delete",
      capturedNavigation,
    );
  }
  const entries = task?.entries || [],
    entry = entries.find((item) => item.id === selected),
    filtered = entries.filter((item) => matchesEntry(item, query, filter));
  async function refreshEntry() {
    if (!entry || !task || writing.current) return;
    const captured = entry,
      capturedNavigation = navigation.current;
    const description =
      task.mode === "live"
        ? "将重新查询本条文献的全部已启用来源：Crossref、DOI / DataCite、arXiv、Semantic Scholar，以及已配置的 DeepSeek / 大模型和已启用的 CNKI 分支。可能产生费用；刷新会创建新任务，不改写旧证据。"
        : "将重新生成本条文献的模拟证据，不发起外部请求。刷新会创建新任务，不改写历史证据。";
    if (
      await confirm(
        "刷新 " + captured.citation_key + "？",
        description,
        "刷新当前条目",
      )
    )
      await operation(
        "/api/entries/" + encodeURIComponent(captured.id) + "/refresh",
        "已创建条目刷新任务",
        { sources: ["all"] },
        false,
        capturedNavigation,
      );
  }
  const total = task?.total || entries.length,
    completed = task?.completed || 0,
    progress = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  return (
    <>
      <a className="skip-link" href="#workspace">
        跳至工作区
      </a>
      <div className="app-shell">
        <aside className="sidebar" aria-label="任务导航">
          <a className="brand" href="/" aria-label="cite-audit 首页">
            <span className="brand-mark">
              c<span>:</span>
            </span>
            <span>cite-audit</span>
          </a>
          <Button className="primary new-task" icon="plus" onClick={openUpload}>
            上传 BibTeX
          </Button>
          <div className="sidebar-heading">
            <span>检查记录</span>
            <span className="subtle">{tasks.length}</span>
          </div>
          <nav className="task-list" aria-label="历史任务">
            {tasks.length ? (
              tasks.map((item) => (
                <button
                  key={item.id}
                  className={
                    "history-item" + (task?.id === item.id ? " active" : "")
                  }
                  aria-current={task?.id === item.id ? "true" : undefined}
                  onClick={() => selectTask(item.id)}
                  title={item.filename}
                >
                  <span className="history-title">
                    <Icon name="document" />
                    <span>{item.filename || "未命名文件"}</span>
                  </span>
                  <span className="history-meta">
                    <span>
                      {formatDate(item.created_at)}
                      {item.mode === "mock" ? " · 演示" : ""}
                    </span>
                    <span
                      className={
                        "history-status" + (isRunning(item) ? " running" : "")
                      }
                    >
                      {statusLabels[item.status] || item.status}
                    </span>
                  </span>
                </button>
              ))
            ) : (
              <p className="sidebar-empty">暂无检查记录</p>
            )}
          </nav>
          <div className="sidebar-footer">
            <span className="local-indicator">
              <span />
              本地工作区
            </span>
            <Button
              className="quiet"
              icon="settings"
              aria-label="打开设置"
              onClick={() => setDialog({ kind: "settings" })}
            >
              设置
            </Button>
          </div>
        </aside>
        <main id="workspace" className="workspace" tabIndex={-1}>
          <header className="workspace-header">
            <div>
              <p className="eyebrow">EVIDENCE FIRST</p>
              <h1>{task?.filename || "引文，逐条有据。"}</h1>
              <div className="header-meta">
                {task ? (
                  <>
                    <span>{formatDate(task.created_at, true)}</span>
                    <span>{statusLabels[task.status] || task.status}</span>
                    {task.parent_id && <span>增量复查</span>}
                  </>
                ) : (
                  "从一份 BibTeX 开始。"
                )}
              </div>
            </div>
            {task && (
              <div className="header-actions">
                <a
                  className="button"
                  href={`/api/tasks/${encodeURIComponent(task.id)}/source`}
                  download
                >
                  <Icon name="download" />
                  原文件
                </a>
                <a
                  className="button"
                  href={`/api/tasks/${encodeURIComponent(task.id)}/report`}
                  download
                >
                  <Icon name="document" />
                  检查报告
                </a>
                {isRunning(task) ? (
                  <Button
                    disabled={busy}
                    onClick={() => void taskAction("cancel")}
                  >
                    取消
                  </Button>
                ) : (
                  <>
                    <Button icon="upload" onClick={openUpload}>
                      复查
                    </Button>
                    <Button
                      disabled={busy}
                      className="quiet"
                      icon="refresh"
                      onClick={() => void taskAction("refresh-stale")}
                    >
                      刷新过期
                    </Button>
                    {!!task.failed && (
                      <Button
                        disabled={busy}
                        className="quiet"
                        onClick={() => void taskAction("retry")}
                      >
                        重试失败
                      </Button>
                    )}
                    <Button
                      disabled={busy}
                      className="quiet danger-text"
                      onClick={() => void taskAction("delete")}
                    >
                      删除
                    </Button>
                  </>
                )}
              </div>
            )}
          </header>
          {connectionError && (
            <div className="banner error" role="alert">
              {connectionError}
              <Button className="small" onClick={() => void reconnect()}>
                重试连接
              </Button>
            </div>
          )}
          {!task ? (
            <section className="welcome">
              <div className="welcome-art" aria-hidden="true">
                <div className="paper paper-back" />
                <div className="paper">
                  <span className="paper-key">{"@article{"}</span>
                  <span className="paper-line long" />
                  <span className="paper-line" />
                  <span className="paper-line short" />
                  <span className="paper-end">{"}"}</span>
                  <span className="paper-check">✓</span>
                </div>
              </div>
              <h2>让每一处修正都有出处</h2>
              <p>比较原值、核验来源，保留你对文献的判断。</p>
              <Button className="primary" icon="upload" onClick={openUpload}>
                上传 .bib 文件
              </Button>
              <span className="welcome-note">原文件始终保留，不自动改写。</span>
              <a className="text-link sample-link" href="/examples/demo.bib">
                下载示例 BibTeX
              </a>
            </section>
          ) : (
            <section>
              {task.mode === "mock" && (
                <div className="banner demo">
                  <Icon name="alert" />
                  <span>
                    <strong>离线演示</strong> ·
                    所有模拟证据均非真实检索结果，请勿据此修改文献。
                  </span>
                </div>
              )}
              <section className="metrics" aria-label="任务进度">
                {[
                  ["进度", `${completed} / ${total}`],
                  ["问题", task.issues || 0],
                  ["失败", task.failed || 0],
                  ["缓存命中", task.cache_hits || 0],
                  ["API 调用", task.api_calls || 0],
                  ["文献", total],
                ].map(([label, value]) => (
                  <div key={label} className="metric">
                    <span className="metric-label">{label}</span>
                    <span className="metric-value">{value}</span>
                  </div>
                ))}
              </section>
              <div
                className="progress-track"
                role="progressbar"
                aria-label="核验进度"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={progress}
              >
                <div id="progress-fill" style={{ width: `${progress}%` }} />
              </div>
              {!!task.parse_issues?.length && (
                <details className="parse-issues">
                  <summary>解析提示 · {task.parse_issues.length}</summary>
                  <ul>
                    {task.parse_issues.map((issue, index) => (
                      <li key={index}>{issueText(issue)}</li>
                    ))}
                  </ul>
                </details>
              )}
              <div className="review-layout">
                <section className="entries-panel" aria-label="文献列表">
                  <div className="list-controls">
                    <label className="search-box">
                      <Icon name="search" />
                      <input
                        type="search"
                        placeholder="搜索标题或 citation key"
                        aria-label="搜索标题或 citation key"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                      />
                    </label>
                    <div className="filter-row">
                      <select
                        aria-label="筛选文献"
                        value={filter}
                        onChange={(event) => setFilter(event.target.value)}
                      >
                        {filters.map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                      <span className="subtle">{filtered.length} 条</span>
                    </div>
                  </div>
                  <div className="entries" role="list">
                    {filtered.length ? (
                      filtered.map((item) => (
                        <div role="listitem" key={item.id}>
                          <button
                            className={
                              "entry-item" +
                              (selected === item.id ? " selected" : "")
                            }
                            aria-pressed={selected === item.id}
                            onClick={() => {
                              setSelected(item.id);
                              setInitialTab("comparison");
                            }}
                          >
                            <span className="entry-key-row">
                              <span className="entry-key">
                                {item.citation_key || "(无 key)"}
                              </span>
                              <span className="entry-year">
                                {item.fields?.year || "—"}
                              </span>
                            </span>
                            <span className="entry-title">
                              {item.fields?.title || "缺少标题"}
                            </span>
                            <span className="entry-flags">
                              <EntryBadges entry={item} />
                            </span>
                          </button>
                        </div>
                      ))
                    ) : (
                      <div className="empty-detail">
                        {query || filter !== "all"
                          ? "没有匹配的文献"
                          : "没有可解析的文献"}
                      </div>
                    )}
                  </div>
                </section>
                <section className="detail-panel" aria-label="文献详情">
                  {entry ? (
                    <EntryDetail
                      key={entry.id}
                      entry={entry}
                      mode={task.mode}
                      initialTab={initialTab}
                      taskRunning={isRunning(task) || busy}
                      notify={notify}
                      onManual={() =>
                        setDialog({
                          kind: "manual",
                          entryId: entry.id,
                          navigation: navigation.current,
                        })
                      }
                      onRefresh={() => void refreshEntry()}
                    />
                  ) : (
                    <div className="empty-detail">选择一条文献查看证据</div>
                  )}
                </section>
              </div>
            </section>
          )}
        </main>
      </div>
      {dialog?.kind === "upload" && (
        <UploadDialog
          parentTaskId={dialog.parentTaskId}
          onClose={() => setDialog(null)}
          onCreated={(id) => onCreated(id, dialog.navigation)}
        />
      )}
      {dialog?.kind === "manual" && (
        <ManualDialog
          entryId={dialog.entryId}
          onClose={() => setDialog(null)}
          onCreated={(id) => onCreated(id, dialog.navigation, true)}
        />
      )}
      {dialog?.kind === "settings" && (
        <SettingsDialog
          onClose={() => setDialog(null)}
          notify={notify}
          confirm={confirm}
        />
      )}
      {confirmation && (
        <ConfirmDialog
          title={confirmation.title}
          description={confirmation.description}
          label={confirmation.label}
          onClose={() => finishConfirmation(false)}
          onConfirm={() => finishConfirmation(true)}
        />
      )}
      {toast && (
        <div
          className={"toast" + (toast.error ? " error" : "")}
          role="status"
          aria-live="polite"
        >
          {toast.message}
        </div>
      )}
    </>
  );
}
