import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowUpRight, Download, FileText, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { download, errorMessage, request, useResource } from '../api';
import type { Page, Task } from '../types';
import { dateTime, executionLabels, ExecutionBadge, ModeBadge, queryString } from '../format';
import { UploadDialog } from '../components/UploadDialog';
import { ActionMenu, Button, EmptyState, InlineError, Loading, Modal, PageHeader, Pagination, SearchInput, Select, useToast } from '../components/ui';
import './tables.css';

export default function TaskList() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [upload, setUpload] = useState(false);
  const [deleting, setDeleting] = useState<Task | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const q = params.get('q') ?? '';
  const status = params.get('status') ?? '';
  const sort = params.get('sort') ?? 'created_desc';
  const page = Math.max(1, Number.parseInt(params.get('page') ?? '1', 10) || 1);
  const requestedSize = Number(params.get('size') ?? 10);
  const size = [10, 20, 50].includes(requestedSize) ? requestedSize : 10;
  const filtered = Boolean(q || status);

  const set = (values: Record<string, string | number>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) => {
      if (value === '') next.delete(key); else next.set(key, String(value));
    });
    if (!('page' in values)) next.set('page', '1');
    setParams(next, { replace: true });
  };
  const { data, loading, error, refresh } = useResource<Page<Task>>(
    `/tasks?${queryString({ q, execution_status: status, sort, page, page_size: size })}`, true,
  );
  useEffect(() => {
    if (!data || loading) return;
    const lastPage = Math.max(1, Math.ceil(data.total / size));
    if (page <= lastPage) return;
    const next = new URLSearchParams(params);
    next.set('page', String(lastPage));
    setParams(next, { replace: true });
  }, [data, loading, page, size, params, setParams]);
  const remove = async () => {
    if (!deleting || busy) return;
    setBusy(true);
    try {
      await request(`/tasks/${deleting.id}`, { method: 'DELETE' });
      setDeleting(null);
      toast('已删除本次任务；共享文献知识保留。');
      refresh();
    } catch (e) { toast(errorMessage(e), true); }
    finally { setBusy(false); }
  };
  const save = async (path: string, filename: string) => {
    try { await download(path, filename); }
    catch (e) { toast(errorMessage(e), true); }
  };

  return <div className="admin-table-page">
    <PageHeader title="Bib 核查任务" actions={
      <Button variant="primary" onClick={() => setUpload(true)}><Plus size={16} />上传 Bib 文件</Button>
    } />
    <div className="data-table-toolbar" role="toolbar" aria-label="任务筛选">
      <div className="data-table-filters">
        <SearchInput value={q} onChange={value => set({ q: value })} placeholder="搜索任务、文件名…" label="搜索任务" />
        <Select aria-label="执行状态筛选" value={status} onValueChange={value => set({ status: value })}
          options={[{ value: '', label: '全部状态' }, ...Object.entries(executionLabels).map(([value, label]) => ({ value, label }))]} />
        {filtered && <Button variant="ghost" className="filter-reset" onClick={() => set({ q: '', status: '' })}>重置<X size={14} /></Button>}
      </div>
      <div className="data-table-tools">
        <Select aria-label="任务排序" value={sort} onValueChange={value => set({ sort: value })}
          options={[{ value: 'created_desc', label: '创建时间 · 最新' }, { value: 'created_asc', label: '创建时间 · 最早' }, { value: 'updated_desc', label: '最近更新' }]} />
        <Button variant="ghost" className="icon-button" onClick={refresh} aria-label="刷新任务"><RefreshCw size={16} /></Button>
      </div>
    </div>
    {error && <InlineError error={error} retry={refresh} />}
    {loading && !data ? <Loading /> : data && data.items.length > 0 ? <>
      <div className="table-wrap admin-data-table">
        <table className="task-table">
          <caption className="sr-only">按上传文件组织的独立核查任务</caption>
          <thead><tr><th>任务</th><th>核查方式</th><th className="numeric-cell">条目</th><th>执行状态</th><th className="numeric-cell">待关注</th><th>创建时间</th><th className="hide-tablet">更新时间</th><th className="menu-cell"><span className="sr-only">操作</span></th></tr></thead>
          <tbody>{data.items.map(task => <tr key={task.id}>
            <td className="task-name-cell"><div className="row-primary"><span className="file-icon"><FileText size={17} /></span><div>
              <Link className="row-title" to={`/tasks/${task.id}`}>{task.name || task.filename}</Link>
              <div className="table-cell-secondary">{task.filename}</div>
            </div></div></td>
            <td><ModeBadge mode={task.mode} /></td>
            <td className="numeric-cell">{task.summary.total}</td>
            <td><ExecutionBadge status={task.execution_status} /></td>
            <td className="numeric-cell"><span className={task.summary.problem_entries ? 'attention-count' : 'muted'}>{task.summary.problem_entries || '—'}</span></td>
            <td className="table-date">{dateTime(task.created_at)}</td>
            <td className="table-date hide-tablet">{dateTime(task.updated_at)}</td>
            <td className="menu-cell"><ActionMenu label={`操作 ${task.name}`} items={[
              { label: '查看任务', icon: <ArrowUpRight size={15} />, onSelect: () => navigate(`/tasks/${task.id}`) },
              { label: '下载原件', icon: <Download size={15} />, onSelect: () => void save(task.original_url, task.filename) },
              { label: '导出报告', icon: <FileText size={15} />, onSelect: () => void save(task.report_url, `${task.name}.report.json`) },
              { label: '删除任务', icon: <Trash2 size={15} />, destructive: true, onSelect: () => setDeleting(task) },
            ]} /></td>
          </tr>)}</tbody>
        </table>
      </div>
      <Pagination page={page} size={size} total={data.total} onPage={value => set({ page: value })} onSize={value => set({ size: value })} />
    </> : !error && <div className="panel table-empty">
      <EmptyState title={filtered ? '没有匹配的任务' : '从第一份 Bib 开始'} action={filtered
        ? <Button onClick={() => set({ q: '', status: '' })}>清除筛选</Button>
        : <Button variant="primary" onClick={() => setUpload(true)}><Plus size={15} />上传 Bib 文件</Button>} />
    </div>}
    {upload && <UploadDialog onClose={() => setUpload(false)} />}
    {deleting && <Modal title="删除这次核查任务？" onClose={() => { if (!busy) setDeleting(null); }}>
      <div className="modal-body"><p>将删除 <strong>{deleting.name}</strong> 的任务记录与上传文件。共享文献知识和其他任务保留。</p><p className="delete-warning">此操作不可撤销。</p></div>
      <div className="modal-actions"><Button disabled={busy} onClick={() => setDeleting(null)}>取消</Button><Button variant="danger" disabled={busy} onClick={() => void remove()}>{busy ? '删除中…' : '仅删除本次任务'}</Button></div>
    </Modal>}
  </div>;
}
