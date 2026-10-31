import { useState } from 'react';
import { ExternalLink, FileText } from 'lucide-react';
import { errorMessage, request } from '../api';
import type { SourceContent, Version } from '../types';
import { dateTime, providerNames, safeExternal, sourceStatuses, sourceTypes, versionLabels } from '../format';
import { Badge, Button, InlineError, Modal } from './ui';
export function SourceCard({ source, index, active, onField }: { source: SourceContent; index: number; active?: boolean; onField?: (field: string) => void }) {
  const [snapshot, setSnapshot] = useState<{ body: unknown; sha256: string } | null>(null); const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const view = async () => { setBusy(true); setError(null); try { setSnapshot(await request(`/snapshots/${source.snapshot_id}`)); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); } };
  const url = safeExternal(source.url);
  return <section id={`source-${source.id}`} className={`source-card ${active ? 'highlighted' : ''}`}>
    <div className="source-heading"><span className="source-number">{index + 1}</span><div><h3>{providerNames[source.provider] ?? source.provider}</h3><div className="source-type">{sourceTypes[source.source_type] ?? source.source_type}</div></div>{url && <a className="button icon-button compact" href={url} target="_blank" rel="noopener noreferrer" aria-label={`打开 ${source.provider} 原始来源`}><ExternalLink size={14} /></a>}</div>
    <div className="actions"><Badge tone={source.status === 'available' ? 'neutral' : 'warning'}>{sourceStatuses[source.status] ?? source.status}</Badge><span className="small muted">{dateTime(source.fetched_at)}</span></div>
    <div className="source-fields">{Object.entries(source.fields).map(([field, value]) => <button key={field} className="source-field" onClick={() => onField?.(field)} disabled={!onField}><span>{field}</span><span>{value}</span></button>)}</div>
    <details><summary>记录详情</summary><dl className="metadata-grid"><div><dt>记录 ID</dt><dd>{source.record_id}</dd></div><div><dt>关联版本</dt><dd>{source.version_id ?? '归属未确认'}</dd></div><div><dt>最近复核</dt><dd>{dateTime(source.checked_at)}</dd></div><div><dt>提取方式</dt><dd>{source.extraction_method}</dd></div></dl>{url && <a className="source-url" href={url} target="_blank" rel="noopener noreferrer">{url}</a>}{source.warnings.map((warning, i) => <p key={i} className="small muted">{warning}</p>)}</details>
    <div className="source-footer"><Button className="compact" disabled={busy} onClick={() => void view()}><FileText size={14} />{busy ? '读取中…' : '原始快照'}</Button></div>
    {error && <InlineError error={error} />}{snapshot && <Modal title="来源快照 · 只读" wide onClose={() => setSnapshot(null)}><div className="modal-body"><p className="mono small break-any">SHA-256 {snapshot.sha256}</p><pre className="snapshot-body">{typeof snapshot.body === 'string' ? snapshot.body : JSON.stringify(snapshot.body, null, 2)}</pre></div></Modal>}
  </section>;

}
export function VersionBox({ version, label, recommended = false }: { version: Version | null; label: string; recommended?: boolean }) {
  if (!version) return <div className="version-box"><Badge>{label}</Badge><p className="muted small" style={{ marginTop: 12 }}>没有足够证据确认此版本</p></div>;
  const f = version.integrated_fields;
  return <div className={`version-box ${recommended ? 'recommended' : ''}`}><Badge tone={recommended ? 'info' : 'neutral'}>{label} · {versionLabels[version.version_type] ?? version.version_type}</Badge><h3>{f.title || '未提供标题'}</h3><dl>{['author', 'year', 'booktitle', 'journal', 'doi', 'url'].filter(key => f[key]).map(key => <div className="version-property" key={key}><dt>{key}</dt><dd>{f[key]}</dd></div>)}</dl><p className="small muted" style={{ marginTop: 8 }}>整合修订 {version.merge_revision} · {version.source_count} 条来源</p></div>;
}
