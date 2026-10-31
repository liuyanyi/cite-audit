import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { AlertTriangle, ArrowUpRight, CheckCheck, ChevronRight, Download, FileText, Pause, RefreshCw, SearchCheck, X } from 'lucide-react';
import { download, errorMessage, json, request, useResource } from '../api';
import type { CallRecord, EntrySummary, Page, Task } from '../types';
import { costText, dateTime, ExecutionBadge, FactBadge, firstAuthor, ModeBadge, queryString, RecommendationBadge } from '../format';
import { ActionMenu, Button, EmptyState, InlineError, Loading, PageHeader, Pagination, SearchInput, Select, Tabs, useToast } from '../components/ui';
import CallTable from '../components/CallTable';
import ExportDialog from '../components/ExportDialog';
import './tables.css';

export default function TaskDetail() {
  const navigate = useNavigate();
  const { taskId } = useParams();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState('entries');
  const [exporting, setExporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const q = params.get('q') ?? '';
  const identity = params.get('identity') ?? '';
  const recommendation = params.get('recommendation') ?? '';
  const conflict = params.get('conflict') === 'true';
  const sort = params.get('sort') ?? 'ordinal';
  const page = Math.max(1, Number.parseInt(params.get('page') ?? '1', 10) || 1);
  const requestedSize = Number(params.get('size') ?? 10);
  const size = [10, 20, 50].includes(requestedSize) ? requestedSize : 10;
  const filtered = Boolean(q || identity || recommendation || conflict);

  const set = (values: Record<string, string | number>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) => {
      if (value === '') next.delete(key); else next.set(key, String(value));
    });
    if (!('page' in values)) next.set('page', '1');
    setParams(next, { replace: true });
  };
  const clearFilters = () => set({ q: '', identity: '', recommendation: '', conflict: '' });
  const task = useResource<Task>(`/tasks/${taskId}`, true);
  const entries = useResource<Page<EntrySummary>>(
    `/tasks/${taskId}/entries?${queryString({ q, identity_state: identity, recommendation_status: recommendation, has_conflict: conflict || undefined, sort, page, page_size: size })}`, true,
  );
  useEffect(() => {
    if (!entries.data || entries.loading) return;
    const lastPage = Math.max(1, Math.ceil(entries.data.total / size));
    if (page <= lastPage) return;
    const next = new URLSearchParams(params);
    next.set('page', String(lastPage));
    setParams(next, { replace: true });
  }, [entries.data, entries.loading, page, size, params, setParams]);
  const calls = useResource<Page<CallRecord>>(tab === 'calls' ? `/calls?task_id=${taskId}&page_size=100` : null, tab === 'calls');
  const save = async (path: string, filename: string) => {
    try { await download(path, filename); }
    catch (e) { toast(errorMessage(e), true); }
  };
  const act = async (action: 'cancel' | 'retry') => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await request<Task>(`/tasks/${taskId}/${action}`, json({}));
      toast(action === 'cancel' ? '已请求取消，已完成的结果保留。' : '已从原件创建独立新任务，旧结果保留。');
      if (action === 'retry') navigate(`/tasks/${result.id}`);
      else { task.refresh(); entries.refresh(); }
    } catch (e) { toast(errorMessage(e), true); }
    finally { setBusy(false); }
  };
  if (!task.data) return task.error ? <InlineError error={task.error} retry={task.refresh} /> : <Loading />;
  const t = task.data;
  const s = t.summary;
  const percent = s.total ? Math.round((s.completed / s.total) * 100) : 0;
  const incomplete = s.failed + s.queued + s.running + s.cancelled;

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
      <div className="task-metric"><div className="task-metric-label">明确字段冲突<AlertTriangle /></div><div className="task-metric-value">{s.conflicts}</div></div>
      <div className="task-metric"><div className="task-metric-label">身份未决<SearchCheck /></div><div className="task-metric-value">{s.unresolved}</div></div>
      <div className="task-metric"><div className="task-metric-label">未处理 / 执行失败<Pause /></div><div className="task-metric-value">{incomplete}</div>{s.parse_errors > 0 && <div className="task-metric-note">解析异常 {s.parse_errors} 条</div>}</div>
    </div>
    <div className="task-progress">
      <div className="progress-track" aria-label={`已处理 ${percent}%`}><span className={['partial', 'failed'].includes(t.execution_status) ? 'warning' : undefined} style={{ width: `${percent}%` }} /><span className="danger" style={{ width: `${s.total ? s.failed / s.total * 100 : 0}%` }} /></div>
      <div className="progress-caption"><span>建议正式版 {s.upgrade_available} 条 · 可补全 {s.completion_available} 条</span><span>调用费用 {costText(t.cost)}</span></div>
    </div>
    <div className="task-content-tabs">
      <Tabs label="任务内容" tabs={[{ id: 'entries', label: <>条目列表<span className="inline-count">{s.total}</span></> }, { id: 'calls', label: '调用审计' }, { id: 'file', label: '原件信息' }]} value={tab} onChange={setTab} />
      <div className="actions">
        {['queued', 'running'].includes(t.execution_status) && <Button disabled={busy} onClick={() => void act('cancel')}>取消任务</Button>}
        {['partial', 'failed', 'cancelled'].includes(t.execution_status) && <Button disabled={busy} onClick={() => void act('retry')}><RefreshCw size={14} />从原件创建新任务</Button>}
      </div>
    </div>
    {tab === 'entries' && <>
      <div className="data-table-toolbar" role="toolbar" aria-label="条目筛选">
        <div className="data-table-filters">
          <SearchInput value={q} onChange={value => set({ q: value })} placeholder="搜索标题、作者、key…" label="搜索条目" />
          <Select aria-label="事实状态筛选" value={identity} onValueChange={value => set({ identity: value })} options={[
            { value: '', label: '全部身份状态' }, { value: 'confirmed', label: '身份已确认' }, { value: 'ambiguous', label: '身份有歧义' },
            { value: 'insufficient', label: '证据不足' }, { value: 'not_found', label: '未找到匹配' }, { value: 'source_failed', label: '来源失败' },
          ]} />
          <Select aria-label="建议类型筛选" value={recommendation} onValueChange={value => set({ recommendation: value })} options={[
            { value: '', label: '全部建议' }, { value: 'upgrade_available', label: '建议正式版' }, { value: 'same_version', label: '当前为推荐版本' },
            { value: 'unavailable', label: '暂无可靠建议' }, { value: 'ambiguous', label: '推荐待确认' },
          ]} />
          <Button aria-pressed={conflict} className={conflict ? 'selected-filter' : ''} onClick={() => set({ conflict: conflict ? '' : 'true' })}><AlertTriangle size={14} />字段冲突</Button>
          {filtered && <Button variant="ghost" className="filter-reset" onClick={clearFilters}>重置<X size={14} /></Button>}
        </div>
        <div className="data-table-tools"><Select aria-label="条目排序" value={sort} onValueChange={value => set({ sort: value })} options={[
          { value: 'ordinal', label: '原文顺序' }, { value: 'title', label: '标题' }, { value: 'year', label: '年份' },
        ]} /></div>
      </div>
      {entries.error && <InlineError error={entries.error} retry={entries.refresh} />}
      {entries.loading && !entries.data ? <Loading /> : entries.data?.items.length ? <>
        <div className="table-wrap admin-data-table"><table className="task-citation-table">
          <caption className="sr-only">本次文件引用核查结果</caption>
          <thead><tr><th>#</th><th>引用</th><th>第一作者</th><th>年份</th><th>当前版本核验</th><th>主要建议</th><th className="menu-cell"><span className="sr-only">操作</span></th></tr></thead>
          <tbody>{entries.data.items.map(entry => {
            const href = `/tasks/${t.id}/entries/${entry.id}?${params}`;
            return <tr key={entry.id}>
              <td className="table-index">{entry.ordinal + 1}</td>
              <td className="citation-cell"><Link className="citation-title" title={entry.title} to={href}>{entry.title || '未解析出标题'}</Link><div className="citation-key"><code>{entry.citation_key}</code>{entry.diagnostics.length > 0 && <span className="parse-count">{entry.diagnostics.length} 项解析提示</span>}</div></td>
              <td className="entry-author">{firstAuthor(entry.author)}</td><td className="muted">{entry.year || '—'}</td>
              <td><FactBadge entry={entry} /></td><td><RecommendationBadge status={entry.recommendation_status} /></td>
              <td className="menu-cell"><ActionMenu label={`操作 ${entry.citation_key}`} items={[
                { label: '查看引用详情', icon: <ArrowUpRight size={15} />, onSelect: () => navigate(href) },
                ...(entry.article_id ? [{ label: '在知识库中查看', icon: <SearchCheck size={15} />, onSelect: () => navigate(`/knowledge/${entry.article_id}`) }] : []),
              ]} /></td>
            </tr>;
          })}</tbody>
        </table></div>
        <Pagination total={entries.data.total} page={page} size={size} onPage={value => set({ page: value })} onSize={value => set({ size: value })} />
      </> : <div className="panel table-empty"><EmptyState title="没有匹配的条目" action={filtered ? <Button onClick={clearFilters}>清除筛选</Button> : undefined} /></div>}
    </>}
    {tab === 'calls' && <div className="panel">{calls.error && <InlineError error={calls.error} retry={calls.refresh} />}{calls.loading && !calls.data ? <Loading /> : <CallTable calls={calls.data?.items ?? []} />}</div>}
    {tab === 'file' && <div className="panel task-original-panel"><div className="panel-body"><h2>上传原件</h2><dl className="metadata-grid"><div><dt>文件名</dt><dd>{t.filename}</dd></div><div><dt>编码</dt><dd>{t.encoding}</dd></div><div><dt>字节数</dt><dd>{t.byte_size}</dd></div><div><dt>SHA-256</dt><dd className="mono">{t.original_sha256}</dd></div></dl></div></div>}
    {exporting && <ExportDialog task={t} onClose={() => setExporting(false)} />}
  </div>;
}
