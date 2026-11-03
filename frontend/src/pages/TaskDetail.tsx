import { bibliographicText } from '../bibliographic-text';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { AlertTriangle, ArrowUpRight, CheckCheck, ChevronRight, Download, FileText, Pause, SlidersHorizontal, SearchCheck, SearchX, X } from 'lucide-react';
import { download, errorMessage, json, request, useResource } from '../api';
import type { CallRecord, EntrySummary, Page, Task } from '../types';
import { classificationLabels, costText, dateTime, ExecutionBadge, firstAuthor, ModeBadge, queryString } from '../format';
import { ActionMenu, Button, EmptyState, InlineError, Loading, PageHeader, Pagination, SearchInput, FilterMenu, Select, Tabs, useToast } from '../components/ui';
import CallTable from '../components/CallTable';
import EntryStatus from '../components/EntryStatus';
import RetryDialog from '../components/RetryDialog';
import ExportDialog from '../components/ExportDialog';
import { isLiveTask, overviewChanged, useTaskEvents } from '../task-events';
import './tables.css';

const progressLabels: Record<string, string> = { queued: '排队中', running: '核验中', completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消' };
const conclusions: Record<string, string> = { ...classificationLabels, unsupported: '不支持核验' };

export default function TaskDetail() {
  const navigate = useNavigate();
  const { taskId } = useParams();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState('entries');
  const [exporting, setExporting] = useState(false);
  const [retryTask, setRetryTask] = useState<Task | null>(null);
  const [busy, setBusy] = useState(false);
  const [searchKey, setSearchKey] = useState(0);
  const toast = useToast();
  const q = params.get('q') ?? '';
  const classification = params.get('classification') ?? '';
  const progress = params.get('execution_status')?.split(',').filter(Boolean) ?? [];
  const required = params.get('required') === 'true';
  const conflict = !required && params.get('conflict') === 'true';
  const upgrade = params.get('upgrade') === 'true';
  const sort = params.get('sort') ?? 'ordinal';
  const page = Math.max(1, Number.parseInt(params.get('page') ?? '1', 10) || 1);
  const requestedSize = Number(params.get('size') ?? 10);
  const size = [10, 20, 50].includes(requestedSize) ? requestedSize : 10;
  const filtered = Boolean(progress.length || q || classification || conflict || required || upgrade);

  const set = (values: Record<string, string | number>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) => {
      if (value === '') next.delete(key); else next.set(key, String(value));
    });
    if (!('page' in values)) next.set('page', '1');
    setParams(next, { replace: true });
  };
  const clearFilters = () => { setSearchKey(key => key + 1); set({ execution_status: '', q: '', classification: '', conflict: '', required: '', upgrade: '' }); };
  const task = useResource<Task>(`/tasks/${taskId}`);
  const entries = useResource<Page<EntrySummary>>(
    tab === 'entries' ? `/tasks/${taskId}/entries?${queryString({ q, execution_status: progress.join(','), identity_state: classification === 'unsupported' ? 'unsupported' : undefined, classification: classification === 'unsupported' ? undefined : classification, has_conflict: conflict || undefined, needs_correction: required || undefined, recommendation_status: upgrade ? 'upgrade_available' : undefined, sort, page, page_size: size })}` : null,
  );
  useEffect(() => {
    if (!entries.data || entries.loading) return;
    const lastPage = Math.max(1, Math.ceil(entries.data.total / size));
    if (page <= lastPage) return;
    const next = new URLSearchParams(params);
    next.set('page', String(lastPage));
    setParams(next, { replace: true });
  }, [entries.data, entries.loading, page, size, params, setParams]);
  const calls = useResource<Page<CallRecord>>(tab === 'calls' ? `/calls?task_id=${taskId}&page_size=100` : null);
  const reports = useResource<{ items: { revision: number; report_url: string }[] }>(tab === 'file' ? `/tasks/${taskId}/reports?revision=${task.data?.report_revision ?? 1}` : null);
  useTaskEvents(Boolean(task.data && isLiveTask(task.data)), taskId, changes => {
    if (overviewChanged(changes)) task.refresh();
    if (!changes || changes.some(change => change.changed.includes('entry'))) entries.refresh();
    if (!changes || changes.some(change => change.changed.includes('call'))) calls.refresh();
    if (!changes || changes.some(change => change.changed.includes('task'))) reports.refresh();
  });
  const save = async (path: string, filename: string) => {
    try { await download(path, filename); }
    catch (e) { toast(errorMessage(e), true); }
  };
  const cancel = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const updated = await request<Task>(`/tasks/${taskId}/cancel`, json({}));
      task.setData(updated);
      toast('已请求取消，已完成的结果保留。');
      task.refresh(); entries.refresh(); calls.refresh(); reports.refresh();
    } catch (e) { toast(errorMessage(e), true); }
    finally { setBusy(false); }
  };
  if (!task.data) return task.error ? <InlineError error={task.error} retry={task.refresh} /> : <Loading />;
  const t = task.data;
  const s = t.summary;
  const percent = s.total ? Math.round((s.completed / s.total) * 100) : 0;
  const unprocessed = s.unprocessed ?? s.total - s.completed;

  return <div className="admin-table-page">
    <div className="breadcrumb"><Link to="/tasks">核查任务</Link><ChevronRight size={12} /><span>{t.name}</span></div>
    <PageHeader title={t.name || t.filename} description={<>
      <span>{t.filename}</span><span>·</span><span>{dateTime(t.created_at)}</span>
      <ExecutionBadge status={t.execution_status} /><ModeBadge mode={t.mode} />
    </>} actions={<>
      <Button onClick={() => void save(t.original_url, t.filename)}><Download size={15} />原件</Button>
      <Button onClick={() => void save(t.report_url, `${t.name}.report.json`)}><FileText size={15} />导出报告</Button>
      <Button variant="primary" onClick={() => setExporting(true)}><FileText size={15} />校订预览 / 导出</Button>
    </>} />
    {task.error && <InlineError error={task.error} retry={task.refresh} />}
    {t.mode === 'demo' && <div className="notice warning compact"><AlertTriangle size={15} /><span>演示数据，非在线核验。</span></div>}
    {t.error && <InlineError error={t.error} />}
    {t.warnings.map((warning, i) => <div className="notice warning compact" key={i}>{warning}</div>)}
    <div className="task-overview">
      <div className="task-metric"><div className="task-metric-label">总条目<FileText /></div><div className="task-metric-value">{s.total}</div></div>
      <div className="task-metric"><div className="task-metric-label">已处理<CheckCheck /></div><div className="task-metric-value">{s.completed}<small>{percent}%</small></div></div>
      <div className="task-metric"><div className="task-metric-label">未找到匹配<SearchX /></div><div className="task-metric-value">{s.not_found ?? '—'}</div></div>
      <div className="task-metric"><div className="task-metric-label">有字段冲突的<AlertTriangle /></div><div className="task-metric-value">{s.conflicts}</div></div>
      <div className="task-metric"><div className="task-metric-label">未处理的<Pause /></div><div className="task-metric-value">{unprocessed}</div>{s.parse_errors > 0 && <div className="task-metric-note">解析异常 {s.parse_errors} 条</div>}</div>
    </div>
    <div className="task-progress">
      <div className="progress-track" aria-label={`已处理 ${percent}%`}><span className={['partial', 'failed'].includes(t.execution_status) ? 'warning' : undefined} style={{ width: `${percent}%` }} /><span className="danger" style={{ width: `${s.total ? s.failed / s.total * 100 : 0}%` }} /></div>
      <div className="progress-caption"><span>建议正式版 {s.upgrade_available} 条 · 可补全 {s.completion_available} 条</span><span>调用费用 {costText(t.cost)}</span></div>
    </div>
    <div className="task-content-tabs">
      <Tabs label="任务内容" tabs={[{ id: 'entries', label: <>条目列表<span className="inline-count">{s.total}</span></> }, { id: 'calls', label: '调用审计' }, { id: 'file', label: '原件信息' }]} value={tab} onChange={setTab} />
      <div className="actions">
        {['queued', 'running'].includes(t.execution_status) && <Button disabled={busy} onClick={() => void cancel()}>取消任务</Button>}
        {['completed', 'partial', 'failed', 'cancelled'].includes(t.execution_status) && <Button disabled={busy || isLiveTask(t)} aria-haspopup="dialog" onClick={() => setRetryTask(t)}><SlidersHorizontal size={14} />重跑设置…</Button>}

      </div>
    </div>
    {tab === 'entries' && <>
      <div className="data-table-toolbar" role="toolbar" aria-label="条目筛选">
        <div className="data-table-filters">
          <SearchInput key={searchKey} value={q} onChange={value => set({ q: value })} debounceMs={300} placeholder="搜索标题、作者、key…" label="搜索条目" />
          <div className="task-filter-options">
            <FilterMenu label="核验进度" selected={progress.map(key => progressLabels[key]).filter(Boolean)} items={Object.entries(progressLabels).map(([key, label]) => ({
              label, checked: progress.includes(key), select: () => set({ execution_status: (progress.includes(key) ? progress.filter(item => item !== key) : [...progress, key]).join(',') }),
            }))} />
            <FilterMenu label="核验结论" selected={classification ? [conclusions[classification]] : []} items={Object.entries(conclusions).map(([key, label]) => ({
              label, checked: key === classification, select: () => set({ classification: key === classification ? '' : key }),
            }))} />
            <FilterMenu label="字段与版本" selected={[conflict && '字段冲突', required && '关键字段须修改', upgrade && '找到新版本'].filter((v): v is string => !!v)} items={[
              { label: '不限字段', checked: !conflict && !required, select: () => set({ conflict: '', required: '' }) },
              { label: '字段冲突', checked: conflict, select: () => set({ conflict: 'true', required: '' }) },
              { label: '关键字段须修改', checked: required, select: () => set({ required: 'true', conflict: '' }) },
              { label: '找到新版本', checked: upgrade, select: () => set({ upgrade: upgrade ? '' : 'true' }) },
            ]} />
            {filtered && <Button variant="ghost" className="filter-reset" onClick={clearFilters}>清除筛选<X size={14} /></Button>}
          </div>
        </div>
        <div className="data-table-tools"><Select aria-label="条目排序" value={sort} onValueChange={value => set({ sort: value })} options={[
          { value: 'ordinal', label: '原文顺序' }, { value: 'title', label: '标题' }, { value: 'year', label: '年份' },
        ]} /></div>
      </div>
      {filtered && entries.data && <small className="muted">匹配 {entries.data.total} / {s.total} 条</small>}
      {entries.error && <InlineError error={entries.error} retry={entries.refresh} />}
      {entries.loading && !entries.data ? <Loading /> : entries.data?.items.length ? <>
        <div className="table-wrap admin-data-table"><table className="task-citation-table">
          <caption className="sr-only">本次文件引用核查结果</caption>
          <thead><tr><th>状态</th><th>引用</th><th>第一作者</th><th>年份</th><th className="menu-cell"><span className="sr-only">操作</span></th></tr></thead>
          <tbody>{entries.data.items.map(entry => {
            const href = `/tasks/${t.id}/entries/${entry.id}?${params}`;
            return <tr key={entry.id}>
              <EntryStatus entry={entry} />
              <td className="citation-cell"><Link className="citation-title" title={bibliographicText(entry.title)} to={href}>{bibliographicText(entry.title) || '未解析出标题'}</Link><div className="citation-key"><code className="citation-key-pill">{entry.citation_key}</code>{(entry.journal || entry.booktitle) && <span className="citation-venue" title={bibliographicText(entry.journal || entry.booktitle)}>{bibliographicText(entry.journal || entry.booktitle)}</span>}{entry.diagnostics.length > 0 && <span className="parse-count">{entry.diagnostics.length} 项解析提示</span>}</div></td>
              <td className="entry-author">{bibliographicText(firstAuthor(entry.author), 'author')}</td><td className="muted">{entry.year || '—'}</td>
              <td className="menu-cell"><ActionMenu label={`操作 ${entry.citation_key}`} items={[
                { label: '查看引用详情', icon: <ArrowUpRight size={15} />, onSelect: () => navigate(href) },
                ...(entry.article_id ? [{ label: '在知识库中查看', icon: <SearchCheck size={15} />, onSelect: () => navigate(`/knowledge/${entry.article_id}`) }] : []),
              ]} /></td>
            </tr>;
          })}</tbody>
        </table></div>
        <Pagination total={entries.data.total} page={page} size={size} onPage={value => set({ page: value })} onSize={value => set({ size: value })} />
      </> : <div className="panel table-empty"><EmptyState title={progress.join() === 'queued' && !q && !classification && !conflict && !required && !upgrade ? '当前没有排队中的条目' : '没有匹配的条目'} action={filtered ? <Button onClick={clearFilters}>清除筛选</Button> : undefined} /></div>}
    </>}
    {tab === 'calls' && <div className="panel">{calls.error && <InlineError error={calls.error} retry={calls.refresh} />}{calls.loading && !calls.data ? <Loading /> : <CallTable calls={calls.data?.items ?? []} />}</div>}
    {tab === 'file' && <div className="panel task-original-panel"><div className="panel-body"><h2>上传原件</h2><dl className="metadata-grid"><div><dt>文件名</dt><dd>{t.filename}</dd></div><div><dt>编码</dt><dd>{t.encoding}</dd></div><div><dt>字节数</dt><dd>{t.byte_size}</dd></div><div><dt>SHA-256</dt><dd className="mono">{t.original_sha256}</dd></div></dl><h2>核查报告</h2>{reports.error && <InlineError error={reports.error} retry={reports.refresh} />}{reports.loading && !reports.data ? <Loading /> : reports.data?.items.map(report => <div key={report.revision}><Button onClick={() => void save(report.report_url, `${t.name}.report-v${report.revision}.json`)}>下载第 {report.revision} 次核查报告</Button></div>)}</div></div>}
    {retryTask && <RetryDialog task={retryTask} classification={classification} onClose={() => { setRetryTask(null); task.refresh(); }}
      onStarted={updated => { setRetryTask(null); task.setData(updated); task.refresh(); entries.refresh(); calls.refresh(); reports.refresh(); toast('已在当前任务中重新核查，历史报告已保留。'); }} />}
    {exporting && <ExportDialog task={t} onClose={() => setExporting(false)} />}
  </div>;
}
