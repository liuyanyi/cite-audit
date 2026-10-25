import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Modal } from "./Dialogs";
import { Badge, Button, str } from "./ui";

export interface BibChange {
  field: string;
  original?: unknown;
  suggested?: unknown;
  judgment?: string;
  evidence_ids?: Array<string | number>;
  reason?: string;
}
interface BibCandidate {
  bibtex: string;
  changes: BibChange[];
}
export interface BibPreviewEntry {
  entry_id: string;
  citation_key: string;
  eligible: boolean;
  reason?: string;
  original: string;
  current: BibCandidate | null;
  publication: BibCandidate | null;
}
interface BibPreview {
  task_id: string;
  filename: string;
  entries: BibPreviewEntry[];
  warnings: unknown[];
}
interface ExportSelection {
  entry_id: string;
  choice: "current" | "publication";
  fields?: string[];
}
interface ExportResult {
  filename: string;
  bibtex: string;
  changes: unknown[];
  warnings: unknown[];
  source_sha256: string;
}
function download(text: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function BibExportDialog({
  taskId,
  onClose,
  onInspect,
  confirm,
}: {
  taskId: string;
  onClose: () => void;
  onInspect: (id: string) => void;
  confirm: (
    title: string,
    description: string,
    label?: string,
  ) => Promise<boolean>;
}) {
  const [preview, setPreview] = useState<BibPreview | null>(null);
  const [selected, setSelected] = useState<Record<string, ExportSelection>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [result, setResult] = useState<ExportResult | null>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  const scope = useRef(0);
  useEffect(() => {
    mounted.current = true;
    scope.current += 1;
    pending.current = false;
    setBusy(false);
    let active = true;
    setLoading(true);
    setError("");
    setPreview(null);
    setSelected({});
    setResult(null);
    api<BibPreview>(`/api/tasks/${encodeURIComponent(taskId)}/export-preview`)
      .then((value) => {
        if (active) setPreview(value);
      })
      .catch((failure) => {
        if (active)
          setError(
            failure instanceof Error ? failure.message : "无法加载整库修改建议",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      mounted.current = false;
    };
  }, [taskId, refresh]);
  async function choose(
    entry: BibPreviewEntry,
    choice: "original" | "current" | "publication",
  ) {
    const generation = scope.current;
    if (
      choice === "publication" &&
      !(await confirm(
        `将 ${entry.citation_key} 切换为正式版？`,
        "这不是普通字段补全。请确认正文不依赖预印本特有内容；只修改本次导出，不覆盖上传原文件。",
        "采用正式版",
      ))
    )
      return;
    if (!mounted.current || generation !== scope.current) return;
    setResult(null);
    setSelected((current) => {
      const next = { ...current };
      if (choice === "original") delete next[entry.entry_id];
      else
        next[entry.entry_id] = {
          entry_id: entry.entry_id,
          choice,
          ...(choice === "current"
            ? {
                fields: [
                  ...new Set(
                    entry.current?.changes.map((change) => change.field) || [],
                  ),
                ],
              }
            : {}),
        };
      return next;
    });
  }
  function toggleField(
    entry: BibPreviewEntry,
    field: string,
    checked: boolean,
  ) {
    setResult(null);
    setSelected((current) => {
      const next = { ...current };
      const old = current[entry.entry_id];
      const fields = checked
        ? [...new Set([...(old?.fields || []), field])]
        : (old?.fields || []).filter((item) => item !== field);
      if (fields.length)
        next[entry.entry_id] = {
          entry_id: entry.entry_id,
          choice: "current",
          fields,
        };
      else delete next[entry.entry_id];
      return next;
    });
  }
  function selectPreset(rich: boolean) {
    if (!preview || busy) return;
    setResult(null);
    setSelected((current) => {
      const next = { ...current };
      for (const entry of preview.entries) {
        if (
          !entry.eligible ||
          !entry.current ||
          current[entry.entry_id]?.choice === "publication"
        )
          continue;
        const fields = [
          ...new Set(entry.current.changes.map((change) => change.field)),
        ].filter(
          (field) =>
            rich || !["abstract", "keywords"].includes(field.toLowerCase()),
        );
        if (fields.length)
          next[entry.entry_id] = {
            entry_id: entry.entry_id,
            choice: "current",
            fields,
          };
        else delete next[entry.entry_id];
      }
      return next;
    });
  }
  async function generate() {
    if (pending.current) return;
    pending.current = true;
    const generation = scope.current;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const output = await api<ExportResult>(
        `/api/tasks/${encodeURIComponent(taskId)}/export`,
        { method: "POST", body: { selections: Object.values(selected) } },
      );
      if (mounted.current && generation === scope.current) setResult(output);
    } catch (failure) {
      if (mounted.current && generation === scope.current)
        setError(
          failure instanceof Error ? failure.message : "无法生成导出文件",
        );
    } finally {
      if (generation === scope.current) {
        pending.current = false;
        if (mounted.current) setBusy(false);
      }
    }
  }
  function downloadResult(kind: "bib" | "report") {
    if (!result) return;
    try {
      if (kind === "bib")
        download(
          result.bibtex,
          result.filename,
          "application/x-bibtex;charset=utf-8",
        );
      else
        download(
          JSON.stringify(
            {
              task_id: taskId,
              source_sha256: result.source_sha256,
              changes: result.changes,
              warnings: result.warnings,
            },
            null,
            2,
          ),
          result.filename.replace(/\.bib$/i, "") + "-changes.json",
          "application/json;charset=utf-8",
        );
    } catch {
      setError("下载失败，请重试。已生成的结果仍保留。");
    }
  }
  return (
    <Modal
      id="bib-export-dialog"
      title="整库修订与导出"
      className="bib-export-dialog"
      onClose={onClose}
      busy={busy}
    >
      <div className="bib-export-content">
        {loading && <p role="status">加载修改建议中…</p>}
        {error && (
          <p role="alert" className="inline-error">
            {error}
          </p>
        )}
        {!loading && !preview && (
          <Button
            className="small"
            onClick={() => setRefresh((value) => value + 1)}
          >
            重新加载
          </Button>
        )}
        {preview && (
          <>
            <div className="export-overview">
              <span>
                {preview.filename} · {preview.entries.length} 条
              </span>
              <Badge tone="primary">
                已选 {Object.keys(selected).length} 条修订
              </Badge>
              <span className="subtle">未选条目保留原文；原文件不覆盖。</span>
            </div>
            {!!preview.warnings.length && (
              <div className="notice warning">
                <ul>
                  {preview.warnings.map((warning, index) => (
                    <li key={index}>{str(warning)}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="export-downloads">
              <Button
                className="small"
                disabled={busy}
                title="选择当前版本的字段修正，不选新增摘要与关键词；不删除原有字段，也不切换正式版。"
                onClick={() => selectPreset(false)}
              >
                引用必需字段
              </Button>
              <Button
                className="small"
                disabled={busy}
                title="选择当前版本的全部字段修正，含摘要与关键词；不切换正式版。"
                onClick={() => selectPreset(true)}
              >
                丰富信息
              </Button>
            </div>
            <div className="export-entries">
              {preview.entries.map((entry) => {
                const selection = selected[entry.entry_id] as
                  ExportSelection | undefined;
                const choice = selection?.choice || "original";
                const candidate =
                  choice === "publication" ? entry.publication : entry.current;
                return (
                  <article key={entry.entry_id} className="export-entry">
                    <div className="section-heading">
                      <h3>{entry.citation_key || "无 citation key"}</h3>
                      <Button
                        className="small quiet"
                        disabled={busy}
                        onClick={() => onInspect(entry.entry_id)}
                      >
                        查看证据
                      </Button>
                    </div>
                    {!entry.eligible && (
                      <p className="notice">
                        {entry.reason || "当前条目不能安全改写，将保留原文。"}
                      </p>
                    )}
                    {entry.eligible && (
                      <fieldset
                        className="export-choices"
                        disabled={busy}
                        aria-label={`${entry.citation_key} 的导出选择`}
                      >
                        <label>
                          <input
                            type="radio"
                            name={`choice-${entry.entry_id}`}
                            checked={choice === "original"}
                            onChange={() => void choose(entry, "original")}
                          />
                          保留原文
                        </label>
                        {entry.current && entry.current.changes.length > 0 && (
                          <label>
                            <input
                              type="radio"
                              name={`choice-${entry.entry_id}`}
                              checked={choice === "current"}
                              onChange={() => void choose(entry, "current")}
                            />
                            采用当前版本修正
                          </label>
                        )}
                        {entry.publication && (
                          <label>
                            <input
                              type="radio"
                              name={`choice-${entry.entry_id}`}
                              checked={choice === "publication"}
                              onChange={() => void choose(entry, "publication")}
                            />
                            改用正式版
                          </label>
                        )}
                      </fieldset>
                    )}
                    {entry.eligible &&
                      candidate &&
                      candidate.changes.length > 0 && (
                        <div className="export-table-wrap">
                          <table className="field-table">
                            <thead>
                              <tr>
                                <th>采纳</th>
                                <th>字段</th>
                                <th>原值</th>
                                <th>建议值 / 依据</th>
                              </tr>
                            </thead>
                            <tbody>
                              {candidate.changes.map((change, index) => (
                                <tr key={`${change.field}-${index}`}>
                                  <td>
                                    {choice === "publication" ? (
                                      <span>整条</span>
                                    ) : (
                                      <input
                                        type="checkbox"
                                        aria-label={`${entry.citation_key}: ${change.field}`}
                                        checked={
                                          choice === "current" &&
                                          !!selection?.fields?.includes(
                                            change.field,
                                          )
                                        }
                                        disabled={busy}
                                        onChange={(event) =>
                                          toggleField(
                                            entry,
                                            change.field,
                                            event.target.checked,
                                          )
                                        }
                                      />
                                    )}
                                  </td>
                                  <td className="field-name">{change.field}</td>
                                  <td>
                                    <span className="old-value">
                                      {str(change.original) || "—"}
                                    </span>
                                  </td>
                                  <td>
                                    <span className="suggested-value">
                                      {str(change.suggested) || "移除"}
                                    </span>
                                    {change.reason && (
                                      <span className="assessment-reason">
                                        {change.reason}
                                      </span>
                                    )}
                                    {!!change.evidence_ids?.length && (
                                      <span className="source-meta">
                                        证据 {change.evidence_ids.join(", ")}
                                      </span>
                                    )}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    <details>
                      <summary>原始条目{candidate ? "与完整候选" : ""}</summary>
                      <pre className="code-box">{entry.original}</pre>
                      {candidate && (
                        <>
                          <p className="subtle">
                            完整候选；导出时仅应用已选修订。
                          </p>
                          <pre className="code-box">{candidate.bibtex}</pre>
                        </>
                      )}
                    </details>
                  </article>
                );
              })}
            </div>
            {result && (
              <section className="export-result">
                <h3>导出已生成</h3>
                <p className="subtle">
                  {result.filename} · {result.changes.length} 条修改记录
                </p>
                {!!result.warnings.length && (
                  <ul className="notice warning">
                    {result.warnings.map((warning, index) => (
                      <li key={index}>{str(warning)}</li>
                    ))}
                  </ul>
                )}
                <div className="export-downloads">
                  <Button
                    icon="download"
                    className="primary"
                    onClick={() => downloadResult("bib")}
                  >
                    下载完整 Bib
                  </Button>
                  <Button
                    icon="document"
                    onClick={() => downloadResult("report")}
                  >
                    下载修改清单
                  </Button>
                </div>
                <details>
                  <summary>查看导出内容</summary>
                  <pre className="code-box">{result.bibtex}</pre>
                </details>
              </section>
            )}
          </>
        )}
      </div>
      <div className="dialog-footer">
        <Button onClick={onClose} disabled={busy}>
          关闭
        </Button>
        <Button
          className="primary"
          disabled={loading || busy || !preview}
          onClick={() => void generate()}
        >
          {busy ? "生成中…" : "生成导出"}
        </Button>
      </div>
    </Modal>
  );
}
