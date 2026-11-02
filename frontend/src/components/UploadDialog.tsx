import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileText, Upload, X } from 'lucide-react';
import { ApiError, errorMessage, request, useResource } from '../api';
import { bytes } from '../format';
import type { Settings, Task } from '../types';
import { Button, InlineError, Modal } from './ui';

interface BudgetWarning { entry_count: number; entry_request_budget: number; request_budget: number; maximum_requests: number }

export function UploadDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const selectionRevision = useRef(0);
  const totalBudgetInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [mode, setMode] = useState<'demo' | 'online'>('demo');
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [validFile, setValidFile] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [budgetWarning, setBudgetWarning] = useState<BudgetWarning | null>(null);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const { data: settings } = useResource<Settings>('/settings');
  const limit = settings?.runtime?.max_upload_bytes ?? 5242880;
  const runtimeFields = [
    { key: 'entry_request_budget', label: '每条核验请求上限' },
    { key: 'request_budget', label: '任务请求总上限' },
    { key: 'timeout_seconds', label: '单次超时（秒）' },
    { key: 'max_retries', label: '最大重试次数' },
  ] as const;

  useEffect(() => {
    if (optionsOpen && !budgetWarning) totalBudgetInput.current?.focus();
  }, [optionsOpen, budgetWarning]);

  const chooseFile = async (selected: File | null) => {
    const revision = ++selectionRevision.current;
    const previousName = file?.name.replace(/\.bib$/i, '') ?? '';
    setFile(selected);
    setError(null);
    setChecking(false);
    setValidFile(false);
    if (!selected) { if (name === previousName) setName(''); return; }
    if (!name || name === previousName) setName(selected.name.replace(/\.bib$/i, ''));
    if (!selected.name.toLowerCase().endsWith('.bib')) { setError('请选择 .bib 文件。'); return; }
    if (!selected.size) { setError('文件为空，请重新选择。'); return; }
    if (selected.size > limit) { setError(`文件不能超过 ${bytes(limit)}。`); return; }
    setChecking(true);
    try {
      const content = await new Promise<ArrayBuffer>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as ArrayBuffer);
        reader.onerror = () => reject(reader.error);
        reader.onabort = () => reject(new Error('File reading cancelled'));
        reader.readAsArrayBuffer(selected);
      });
      new TextDecoder('utf-8', { fatal: true }).decode(content);
      if (revision === selectionRevision.current) setValidFile(true);
    } catch {
      if (revision === selectionRevision.current) setError('无法读取 UTF-8 文件，请检查编码后重新选择。');
    } finally {
      if (revision === selectionRevision.current) setChecking(false);
    }
  };

  const createTask = async (confirmBudget = false) => {
    if (!file || !settings || busy || checking || !validFile) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    const config = Object.fromEntries(runtimeFields.map(({ key }) => [key, overrides[key] ?? settings.runtime[key]]));
    form.set('file', file); form.set('name', name.trim()); form.set('mode', mode);
    form.set('config', JSON.stringify(config));
    if (confirmBudget) form.set('confirm_budget', 'true');
    try {
      const task = await request<Task>('/tasks', { method: 'POST', body: form });
      onClose(); navigate(`/tasks/${task.id}`);
    } catch (e) {
      const details = e instanceof ApiError && e.code === 'budget_confirmation_required' ? e.details : undefined;
      if (details && ['entry_count', 'entry_request_budget', 'request_budget', 'maximum_requests'].every(key => typeof details[key] === 'number')) {
        setOverrides(config);
        setBudgetWarning(details as unknown as BudgetWarning);
      } else setError(errorMessage(e));
    }
    finally { setBusy(false); }
  };

  if (budgetWarning) return <Modal title="理论请求上限超过任务预算" onClose={() => { if (!busy) { setBudgetWarning(null); setError(null); } }}>
    <div className="modal-body">
      {error && <InlineError error={error} />}
      <p>{budgetWarning.entry_count} 条 × 每条最多 {budgetWarning.entry_request_budget} 次 = {budgetWarning.maximum_requests} 次，超过任务请求总上限 {budgetWarning.request_budget} 次。</p>
      <p>这是理论上限，实际请求可能因缓存复用或提前完成而减少。按当前预算继续，达到总上限后部分条目可能无法完成核验，已取得的证据会保留。</p>
    </div>
    <div className="modal-actions"><Button disabled={busy} onClick={() => { setBudgetWarning(null); setError(null); setOptionsOpen(true); }}>调整预算</Button><Button variant="primary" disabled={busy} onClick={() => void createTask(true)}>{busy ? '创建中…' : '按当前预算继续'}</Button></div>
  </Modal>;

  return <Modal title="上传 Bib 文件" onClose={() => { if (!busy) onClose(); }}>
    <form onSubmit={event => { event.preventDefault(); void createTask(); }}>
      <div className="modal-body">
        {error && <InlineError error={error} />}
        <input ref={input} id="bib-file" className="sr-only" tabIndex={-1} type="file" aria-label="完整 BibTeX 文件" accept=".bib,application/x-bibtex" disabled={busy}
          onChange={e => { const selected = e.target.files?.[0]; if (selected) void chooseFile(selected); e.target.value = ''; }} />
        <div className={`upload-zone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
          onDragOver={e => { e.preventDefault(); if (!busy) setDragging(true); }}
          onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false); }}
          onDrop={e => { e.preventDefault(); setDragging(false); if (busy) return; if (e.dataTransfer.files.length !== 1) { setError('请一次选择一份 Bib 文件。'); return; } void chooseFile(e.dataTransfer.files[0]); }}>
          {file ? <>
            <span className="upload-file-icon"><FileText size={24} /></span>
            <div className="upload-file-info" aria-live="polite"><strong>{file.name}</strong><span>{bytes(file.size)}{checking ? ' · 检查中…' : ''}</span></div>
            <Button type="button" disabled={busy} onClick={() => input.current?.click()}>重新选择</Button>
            <Button type="button" variant="ghost" disabled={busy} aria-label="移除已选文件" onClick={() => void chooseFile(null)}><X size={16} /></Button>
          </> : <>
            <span className="upload-file-icon"><Upload size={24} /></span>
            <strong>将 Bib 文件拖到这里</strong>
            <Button type="button" disabled={busy} onClick={() => input.current?.click()} aria-describedby="upload-limit">选择文件</Button>
            <span id="upload-limit" className="upload-limit">.bib · 最大 {bytes(limit)}</span>
          </>}
        </div>
        <div className="form-field"><label htmlFor="task-name">任务名称</label><input id="task-name" value={name} disabled={busy} maxLength={160} placeholder="默认使用文件名" onChange={e => setName(e.target.value)} /></div>
        <div className="form-field"><span className="field-label">核查方式</span><div className="radio-cards">
          <label className={`radio-card ${mode === 'demo' ? 'selected' : ''}`}><input type="radio" name="mode" value="demo" disabled={busy} checked={mode === 'demo'} onChange={() => setMode('demo')} /><span><strong>离线演示</strong><small>合成样例，非真实核验</small></span></label>
          <label className={`radio-card ${mode === 'online' ? 'selected' : ''}`}><input type="radio" name="mode" value="online" disabled={busy} checked={mode === 'online'} onChange={() => setMode('online')} /><span><strong>在线核查</strong><small>查询已启用的文献来源</small></span></label>
        </div></div>
        {mode === 'online' && <p id="online-notice" className="muted">开始核查即同意向已启用的文献来源发送必要查询字段。</p>}
        <details className="upload-options" open={optionsOpen} onToggle={event => setOptionsOpen(event.currentTarget.open)}><summary>请求配置</summary><div className="form-grid">{settings && runtimeFields.map(item => <label className="form-field" key={item.key}><span>{item.label}</span><input ref={item.key === 'request_budget' ? totalBudgetInput : undefined} type="number" disabled={busy} min={settings.limits.runtime[item.key].min} max={settings.limits.runtime[item.key].max} value={overrides[item.key] ?? settings.runtime[item.key]} onChange={e => setOverrides(current => ({ ...current, [item.key]: Number(e.target.value) }))} /></label>)}</div></details>
      </div>
      <div className="modal-actions"><Button onClick={onClose} type="button" disabled={busy}>取消</Button><Button type="submit" variant="primary" aria-describedby={mode === 'online' ? 'online-notice' : undefined} disabled={busy || checking || !validFile || !file || !settings}><Upload size={14} />{busy ? '上传中…' : mode === 'demo' ? '创建演示任务' : '开始在线核查'}</Button></div>
    </form>
  </Modal>;
}
