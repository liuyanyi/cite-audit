import { useEffect, useState } from 'react';
import { Save, Trash2 } from 'lucide-react';
import { Navigate, useParams } from 'react-router-dom';
import { settingsSections } from '../settingsNavigation';
import { errorMessage, json, request, useResource } from '../api';
import type { CallRecord, Page, Settings } from '../types';
import { providerNames } from '../format';
import { Badge, Button, InlineError, Loading, Modal, PageHeader, Pagination, useToast } from '../components/ui';
import CallTable from '../components/CallTable';

const statusLabels: Record<string, string> = { not_tested: '未测试', available: '可访问', unavailable: '不可访问', needs_key: '需要密钥', not_implemented: '尚未接入' };
type Confirmation = { kind: 'model' } | { kind: 'source'; id: string; name: string } | { kind: 'cache' };

export default function SettingsPage() {
  const resource = useResource<Settings>('/settings');
  const [draft, setDraft] = useState<Settings | null>(null);
  const [dirty, setDirty] = useState(false);
  const { section = 'sources' } = useParams();
  const currentSection = settingsSections.find(item => item.id === section);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [callPage, setCallPage] = useState(1);
  const [callStatus, setCallStatus] = useState('');
  const [provider, setProvider] = useState('');
  const toast = useToast();
  const calls = useResource<Page<CallRecord>>(section === 'audit' ? `/calls?page=${callPage}&page_size=20&status=${encodeURIComponent(callStatus)}&provider=${encodeURIComponent(provider)}` : null);
  useEffect(() => { if (resource.data && !dirty) setDraft(resource.data); }, [resource.data, dirty]);
  const change = (update: (current: Settings) => Settings) => { setDraft(current => current ? update(current) : current); setDirty(true); };
  const save = async () => {
    if (!draft || busy) return;
    setBusy(true); setError(null);
    try {
      // Read-only status and key-presence fields are intentionally not submitted.
      const payload = {
        sources: draft.sources.map(source => ({ id: source.id, enabled: source.enabled, ...(source.api_key === undefined ? {} : { api_key: source.api_key }) })),
        model: { enabled: draft.model.enabled, protocol: draft.model.protocol, model: draft.model.model, base_url: draft.model.base_url,
          timeout_seconds: draft.model.timeout_seconds, max_retries: draft.model.max_retries, max_output_tokens: draft.model.max_output_tokens,
          ...(draft.model.api_key === undefined ? {} : { api_key: draft.model.api_key }) },
        runtime: draft.runtime, cache: draft.cache, audit: draft.audit,
      };
      const saved = await request<Settings>('/settings', { method: 'PUT', body: JSON.stringify(payload) });
      setDraft(saved); setDirty(false); resource.refresh(); toast('设置已保存');
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  const execute = async () => {
    if (!confirmation || busy) return;
    setBusy(true); setError(null);
    try {
      if (confirmation.kind === 'cache') {
        await request('/query-cache', { method: 'DELETE' }); toast('查询缓存已清除；知识库与历史报告保留。');
      } else {
        const path = confirmation.kind === 'model' ? '/settings/model/test' : `/settings/sources/${encodeURIComponent(confirmation.id)}/test`;
        const result = await request<{ status?: string; message?: string }>(path, json({ confirmed: true }));
        const failed = ['unavailable', 'failed', 'needs_key', 'not_implemented'].includes(result.status ?? '');
        toast(result.message || (failed ? '连接测试未通过，请查看调用记录。' : '连接测试已完成，请查看调用记录。'), failed);
        resource.refresh(); calls.refresh();
      }
      setConfirmation(null);
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  if (!currentSection) return <Navigate to="/settings/sources" replace />;
  if (!draft) return resource.error ? <InlineError error={resource.error} retry={resource.refresh} /> : <Loading />;
  const numeric = (group: 'runtime' | 'cache', key: string, label: string, min: number, max: number, fixed = false) => {
    const values = draft[group] as unknown as Record<string, number>;
    return <label className="form-field" key={key}>{label}<input type="number" disabled={fixed} min={min} max={max} value={values[key]} onChange={e => change(current => ({ ...current, [group]: { ...current[group], [key]: Number(e.target.value) } }))} /></label>;
  };
  return <>
    <PageHeader title={currentSection.label} actions={<Button variant="primary" disabled={busy || !dirty} onClick={() => void save()}><Save size={14} />{busy ? '处理中…' : '保存设置'}</Button>} />
    {(error || resource.error) && <InlineError error={error || resource.error || ''} />}
    <div className="settings-content">
      <div className="stack">
        {dirty && <div className="notice warning compact">请先保存更改，再测试连接。</div>}
        {section === 'sources' && <section className="panel"><div className="panel-body">
          {draft.sources.map(source => <div key={source.id}>
            <div className="provider-row"><div className="provider-icon">{source.name.slice(0, 2)}</div><div className="provider-info"><h3>{providerNames[source.id] || source.name}</h3>{(!source.implemented || source.requires_key) && <p>{!source.implemented ? '尚未接入' : '需要 API key'}</p>}</div>
              <Badge tone={source.status === 'available' ? 'success' : source.status === 'unavailable' ? 'danger' : 'neutral'}>{statusLabels[source.status] || source.status}</Badge>
              <Button className="compact" disabled={busy || dirty || !source.implemented} onClick={() => setConfirmation({ kind: 'source', id: source.id, name: source.name })}>测试连接</Button>
              <input className="toggle" type="checkbox" aria-label={`启用 ${source.name}`} disabled={!source.implemented || busy} checked={source.enabled} onChange={e => change(current => ({ ...current, sources: current.sources.map(item => item.id === source.id ? { ...item, enabled: e.target.checked } : item) }))} />
            </div>
            {source.implemented && (source.supports_api_key ?? ['openalex', 'semantic_scholar'].includes(source.id)) && <details style={{ paddingBottom: 12 }}><summary className="small">API key · {source.has_api_key ? '已保存' : '未设置'}</summary><label className="form-field" style={{ marginTop: 8 }}>更换 {source.name} 密钥<input type="password" autoComplete="new-password" placeholder="留空不更换" value={source.api_key ?? ''} onChange={e => change(current => ({ ...current, sources: current.sources.map(item => item.id === source.id ? { ...item, api_key: e.target.value || undefined } : item) }))} /></label>{source.has_api_key && <Button className="compact" onClick={() => change(current => ({ ...current, sources: current.sources.map(item => item.id === source.id ? { ...item, api_key: '' } : item) }))}>清除已存密钥（保存后生效）</Button>}{source.api_key === '' && <span className="small muted"> 已标记清除</span>}</details>}
          </div>)}
        </div></section>}
        {section === 'model' && <section className="panel"><div className="panel-body">
          <div className="provider-row"><div className="provider-info"><h2 className="settings-section-title">模型连接测试</h2><p>仅连接测试，核查流程尚未接入。</p></div><input className="toggle" type="checkbox" aria-label="启用模型测试" checked={draft.model.enabled} onChange={e => change(current => ({ ...current, model: { ...current.model, enabled: e.target.checked } }))} /></div>
          <div className="form-grid" style={{ marginTop: 20 }}>
            <label className="form-field">协议<select value={draft.model.protocol} onChange={e => change(current => ({ ...current, model: { ...current.model, protocol: e.target.value } }))}><option value="openai-chat">OpenAI Chat Completions</option><option value="openai-responses">OpenAI Responses</option><option value="ant-messages">Anthropic Messages</option></select></label>
            <label className="form-field">模型 ID<input value={draft.model.model} onChange={e => change(current => ({ ...current, model: { ...current.model, model: e.target.value } }))} /></label>
            <label className="form-field">Base URL<input type="url" value={draft.model.base_url} placeholder="https://…" onChange={e => change(current => ({ ...current, model: { ...current.model, base_url: e.target.value } }))} /></label>
            <label className="form-field">API key · {draft.model.has_api_key ? '已保存' : '未设置'}<input type="password" autoComplete="new-password" value={draft.model.api_key ?? ''} placeholder="留空不更换" onChange={e => change(current => ({ ...current, model: { ...current.model, api_key: e.target.value || undefined } }))} /></label>
            {(['timeout_seconds', 'max_retries', 'max_output_tokens'] as const).map((key, index) => <label key={key} className="form-field">{['超时（秒）', '应用重试次数', '最大输出 tokens'][index]}<input type="number" min={key === 'max_retries' ? 0 : 1} value={draft.model[key]} onChange={e => change(current => ({ ...current, model: { ...current.model, [key]: Number(e.target.value) } }))} /></label>)}
          </div>
          <div className="actions" style={{ marginTop: 16 }}>{draft.model.has_api_key && <Button onClick={() => change(current => ({ ...current, model: { ...current.model, api_key: '' } }))}>清除已存密钥（保存后生效）</Button>}{draft.model.api_key === '' && <span className="small muted">已标记清除</span>}</div>
          <div style={{ marginTop: 20 }}><div>价格来源：MMSP 公开目录 · 未匹配时费用未知。{draft.model.price != null && <details><summary>当前价格快照</summary><pre>{JSON.stringify(draft.model.price, null, 2)}</pre></details>}</div></div>
          <div className="settings-actions"><Button disabled={busy || dirty || !draft.model.enabled || !draft.model.model} onClick={() => setConfirmation({ kind: 'model' })}>测试模型连接（可能计费）</Button></div>
        </div></section>}
        {section === 'runtime' && <section className="panel"><div className="panel-body"><h2 className="settings-section-title">新任务默认配置</h2><div className="form-grid" style={{ marginTop: 20 }}>
          {numeric('runtime', 'request_budget', '请求次数上限', 1, 5000)}
          {numeric('runtime', 'timeout_seconds', '来源超时（秒）', 1, 60)}
          {numeric('runtime', 'max_retries', '来源重试次数', 0, 2)}
          {numeric('runtime', 'concurrency', '并发数（当前固定）', 1, 1, true)}
          {numeric('runtime', 'max_upload_bytes', '上传上限（字节）', 1024, 20000000)}
        </div></div></section>}
        {section === 'cache' && <section className="panel"><div className="panel-body"><h2 className="settings-section-title">缓存有效期</h2><div className="form-grid" style={{ marginTop: 20 }}>
          {numeric('cache', 'positive_ttl_seconds', '成功结果有效期（秒）', 0, 604800)}
          {numeric('cache', 'negative_ttl_seconds', '未命中结果有效期（秒，当前固定）', 300, 300, true)}
          {numeric('cache', 'discovery_ttl_seconds', '版本发现有效期（秒）', 0, 2592000)}
        </div><div className="settings-actions"><Button disabled={busy} onClick={() => setConfirmation({ kind: 'cache' })}><Trash2 size={14} />清除查询缓存</Button></div></div></section>}
        {section === 'audit' && <><section className="panel"><div className="panel-body"><h2 className="settings-section-title">调用审计</h2><div className="stack" style={{ marginTop: 18 }}>
          <label className="flex"><input type="checkbox" disabled checked={draft.audit.enabled} onChange={e => change(current => ({ ...current, audit: { ...current.audit, enabled: e.target.checked } }))} />保存调用审计（始终开启）</label>
          <label className="flex"><input type="checkbox" disabled checked={draft.audit.retain_prompt_text} onChange={e => change(current => ({ ...current, audit: { ...current.audit, retain_prompt_text: e.target.checked } }))} />保存提示文本（当前不保留）</label>
          <div>密钥脱敏 · 费用按币种记录，未知费用不计作零。</div>
        </div></div></section><div className="toolbar"><select aria-label="调用来源筛选" value={provider} onChange={e => { setProvider(e.target.value); setCallPage(1); }}><option value="">全部来源</option>{draft.sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select><select aria-label="调用状态筛选" value={callStatus} onChange={e => { setCallStatus(e.target.value); setCallPage(1); }}><option value="">全部状态</option><option value="success">成功</option><option value="failed">失败</option><option value="rate_limited">限流</option><option value="no_result">未命中</option></select><Button onClick={calls.refresh}>刷新记录</Button></div>{calls.error && <InlineError error={calls.error} retry={calls.refresh} />}<section className="panel">{calls.loading && !calls.data ? <Loading /> : <CallTable calls={calls.data?.items ?? []} />}</section>{calls.data && <Pagination page={callPage} total={calls.data.total} size={20} onPage={setCallPage} />}</>}
      </div>
    </div>
    {confirmation && <Modal title={confirmation.kind === 'cache' ? '清除查询缓存？' : confirmation.kind === 'model' ? '发起真实模型请求？' : `测试 ${confirmation.name}？`} onClose={() => { if (!busy) setConfirmation(null); }}><div className="modal-body"><p>{confirmation.kind === 'cache' ? '仅清除可重新生成的查询缓存。文章、版本、来源快照和历史任务报告不删除。' : confirmation.kind === 'model' ? `将使用已保存的 ${draft.model.model} 配置发起真实 API 请求，可能产生费用。费用以提供方账单为准。` : '将向该数据源发起真实联网请求，并写入调用审计记录。'}</p></div><div className="modal-actions"><Button disabled={busy} onClick={() => setConfirmation(null)}>取消</Button><Button variant="primary" disabled={busy} onClick={() => void execute()}>{busy ? '处理中…' : confirmation.kind === 'cache' ? '确认清除' : '确认发起请求'}</Button></div></Modal>}
  </>;
}
