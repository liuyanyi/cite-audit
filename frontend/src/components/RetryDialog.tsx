import { useState } from 'react';
import { errorMessage, json, request, useResource } from '../api';
import { bibliographicText } from '../bibliographic-text';
import { classificationLabels, entryClassification, queryString } from '../format';
import type { EntryClassification, EntrySummary, Page, Task } from '../types';
import { Button, Checkbox, InlineError, Loading, Modal, Pagination, SearchInput, Tabs } from './ui';

const groups = Object.keys(classificationLabels) as EntryClassification[];
const defaults: EntryClassification[] = ['not_found', 'unverifiable', 'source_failed', 'budget_exhausted'];
export default function RetryDialog({ task, classification, onClose, onStarted }: {
  task: Task; classification: string; onClose: () => void; onStarted: (task: Task) => void;
}) {
  const [scope, setScope] = useState('categories');
  const [categories, setCategories] = useState(() => (groups.includes(classification as EntryClassification)
    ? [classification as EntryClassification] : defaults).filter(key => task.summary[key]));
  const [ids, setIds] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(1);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [entryBudget, setEntryBudget] = useState(String(task.config.entry_request_budget ?? 1));
  const [totalBudget, setTotalBudget] = useState(String(task.config.request_budget ?? 1));
  const entries = useResource<Page<EntrySummary>>(scope === 'entries'
    ? `/tasks/${task.id}/entries?${queryString({ q, page, page_size: 10 })}` : null);
  const count = scope === 'all' ? task.summary.total : scope === 'entries' ? ids.size
    : categories.reduce((n, key) => n + (task.summary[key] ?? 0), 0);
  const select = (keys: string[], checked: boolean) => setIds(prev => {
    const next = new Set(prev);
    keys.forEach(key => checked ? next.add(key) : next.delete(key));
    return next;
  });
  const pageIds = entries.data?.items.map(entry => entry.id) ?? [];
  const validBudget = (v: string, max: number) => Number.isInteger(Number(v)) && Number(v) >= 1 && Number(v) <= max;
  const valid = task.mode === 'demo' || (validBudget(entryBudget, 40) && validBudget(totalBudget, 400000));
  const submit = async () => {
    if (!count || busy || !valid) return;
    setBusy(true); setError('');
    try {
      const updated = await request<Task>(`/tasks/${task.id}/retry`, json({
        scope, revision: task.report_revision ?? 1,
        ...(scope === 'categories' && { categories }), ...(scope === 'entries' && { entry_ids: [...ids] }),
        ...(task.mode === 'online' && { entry_request_budget: Number(entryBudget), request_budget: Number(totalBudget) }),
      }));
      onStarted(updated);
    } catch (e) { setError(errorMessage(e)); setBusy(false); }
  };
  return <Modal title="重跑设置" onClose={() => { if (!busy) onClose(); }}>
    <div className="modal-body stack">
      <Tabs label="重跑范围" value={scope} onChange={setScope} tabs={[
        { id: 'categories', label: '按核查结果' }, { id: 'entries', label: '手动选择条目' }, { id: 'all', label: '全部条目' },
      ]} />
      {scope === 'categories' && <div className="retry-groups">{groups.map(key => <label key={key} className={task.summary[key] ? undefined : "muted"}>
        <Checkbox checked={categories.includes(key)} disabled={!task.summary[key]} onCheckedChange={v => setCategories(prev =>
          v ? [...prev, key] : prev.filter(k => k !== key))} /> {classificationLabels[key]} <span>{task.summary[key] ?? 0}</span>
      </label>)}</div>}
      {scope === 'entries' && <>
        <SearchInput value={q} label="搜索待重跑条目" placeholder="搜索标题、作者、key…" onChange={v => { setQ(v); setPage(1); }} />
        {entries.error ? <InlineError error={entries.error} retry={entries.refresh} /> : entries.loading ? <Loading /> : <>
          <label><Checkbox disabled={!pageIds.length} checked={pageIds.length > 0 && pageIds.every(id => ids.has(id))}
            onCheckedChange={v => select(pageIds, v === true)} /> 选择本页</label>
          {entries.data?.items.map(entry => { const kind = entryClassification(entry); return <label key={entry.id}>
            <Checkbox checked={ids.has(entry.id)} onCheckedChange={v => select([entry.id], v === true)} /> {bibliographicText(entry.title)} <small>{entry.citation_key} · {kind && classificationLabels[kind]}</small>
          </label>; })}
          <Pagination page={page} size={10} total={entries.data?.total ?? 0} onPage={setPage} />
        </>}
      </>}
      {scope === 'all' && <p>重新核查任务中的全部 {task.summary.total} 条。</p>}
      {task.mode === 'online' && <details><summary>请求预算（次数）</summary><div className="stack">
        <label>每条上限 <input type="number" min={1} max={40} value={entryBudget} onChange={e => setEntryBudget(e.target.value)} /></label>
        <label>本轮总上限 <input type="number" min={1} max={400000} value={totalBudget} onChange={e => setTotalBudget(e.target.value)} /></label>
        {!valid && <p role="alert">每条上限为 1–40 次，本轮总上限为 1–400000 次。</p>}
      </div></details>}
      {task.mode === 'online' && count * Number(entryBudget) > Number(totalBudget) && <p className="notice warning">本轮总上限不足以让每条用满配额，部分条目可能无法完成检索。</p>}
      <p className="muted">已选 {count} 条。上一次核查报告将保留。</p>
      {error && <p role="alert">{error}</p>}
    </div>
    <div className="modal-actions"><Button disabled={busy} onClick={onClose}>取消</Button>
      <Button variant="primary" disabled={!count || !valid || busy} onClick={() => void submit()}>{busy ? '正在提交…' : `开始重跑 ${count} 条`}</Button></div>
  </Modal>;
}
