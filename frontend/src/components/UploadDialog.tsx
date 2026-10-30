import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileText, Upload, X } from 'lucide-react';
import { errorMessage, request, useResource } from '../api';
import { bytes } from '../format';
import type { Settings, Task } from '../types';
import { Button, Checkbox, InlineError, Modal } from './ui';

export function UploadDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const selectionRevision = useRef(0);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [mode, setMode] = useState<'demo' | 'online'>('demo');
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [validFile, setValidFile] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [onlineConsent, setOnlineConsent] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const { data: settings } = useResource<Settings>('/settings');
  const limit = settings?.runtime?.max_upload_bytes ?? 5242880;
  const runtimeFields = [
    { key: 'request_budget', label: '请求预算', max: 5000, fallback: 30 },
    { key: 'timeout_seconds', label: '单次超时（秒）', max: 60, fallback: 20 },
    { key: 'max_retries', label: '最大重试次数', max: 2, fallback: 2 },
  ] as const;

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

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file || busy || checking || !validFile || (mode === 'online' && !onlineConsent)) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.set('file', file); form.set('name', name.trim()); form.set('mode', mode);
    form.set('config', JSON.stringify(overrides));
    try {
      const task = await request<Task>('/tasks', { method: 'POST', body: form });
      onClose(); navigate(`/tasks/${task.id}`);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  };

  return <Modal title="上传 Bib 文件" onClose={() => { if (!busy) onClose(); }}>
    <form onSubmit={submit}>
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
        <div className="form-field"><label htmlFor="task-name">任务名称</label><input id="task-name" value={name} maxLength={160} placeholder="默认使用文件名" onChange={e => setName(e.target.value)} /></div>
        <div className="form-field"><span className="field-label">核查方式</span><div className="radio-cards">
          <label className={`radio-card ${mode === 'demo' ? 'selected' : ''}`}><input type="radio" name="mode" value="demo" checked={mode === 'demo'} onChange={() => setMode('demo')} /><span><strong>离线演示</strong><small>合成样例，非真实核验</small></span></label>
          <label className={`radio-card ${mode === 'online' ? 'selected' : ''}`}><input type="radio" name="mode" value="online" checked={mode === 'online'} onChange={() => setMode('online')} /><span><strong>在线核查</strong><small>查询已启用的文献来源</small></span></label>
        </div></div>
        {mode === 'online' && <label className="confirmation-checkbox"><Checkbox checked={onlineConsent} onCheckedChange={value => setOnlineConsent(value === true)} /><span>允许向已启用的来源发送查询所需字段。</span></label>}
        <details className="upload-options"><summary>请求配置</summary><div className="form-grid">{runtimeFields.map(item => <label className="form-field" key={item.key}><span>{item.label}</span><input type="number" min={item.key === 'max_retries' ? 0 : 1} max={item.max} value={overrides[item.key] ?? settings?.runtime?.[item.key] ?? item.fallback} onChange={e => setOverrides(current => ({ ...current, [item.key]: Number(e.target.value) }))} /></label>)}</div></details>
      </div>
      <div className="modal-actions"><Button onClick={onClose} type="button" disabled={busy}>取消</Button><Button type="submit" variant="primary" disabled={busy || checking || !validFile || !file || (mode === 'online' && !onlineConsent)}><Upload size={14} />{busy ? '上传中…' : mode === 'demo' ? '创建演示任务' : '开始在线核查'}</Button></div>
    </form>
  </Modal>;
}
