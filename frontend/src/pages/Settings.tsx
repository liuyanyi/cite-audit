import { useEffect, useState } from 'react';
import { PlugZap, Save, SlidersHorizontal, Trash2 } from 'lucide-react';
import { Navigate, useParams } from 'react-router-dom';
import { settingsSections } from '../settingsNavigation';
import { errorMessage, json, request, useResource } from '../api';
import type { CallRecord, Page, Settings, SourceConfig } from '../types';
import { providerNames } from '../format';
import { Badge, Button, Checkbox, EmptyState, InlineError, Loading, Modal, PageHeader, Pagination, SearchInput, Select, Switch, useToast } from '../components/ui';
import CallTable from '../components/CallTable';
import './settings.css';

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
  const [sourceSearch, setSourceSearch] = useState('');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [sourceEditor, setSourceEditor] = useState<SourceConfig | null>(null);
  const [testedSources, setTestedSources] = useState<Record<string, string>>({});
  const toast = useToast();
  const calls = useResource<Page<CallRecord>>(section === 'audit' ? `/calls?page=${callPage}&page_size=20&status=${encodeURIComponent(callStatus)}&provider=${encodeURIComponent(provider)}` : null);
  useEffect(() => { if (resource.data && !dirty) setDraft(resource.data); }, [resource.data, dirty]);
  useEffect(() => { setSourceEditor(null); setConfirmation(null); }, [section]);
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
        if (confirmation.kind === 'source' && result.status) setTestedSources(current => ({ ...current, [confirmation.id]: result.status! }));
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
  const sources = draft.sources.filter(source => (providerNames[source.id] || source.name).toLowerCase().includes(sourceSearch.toLowerCase()) && (sourceFilter === 'all' || (sourceFilter === 'enabled' ? source.enabled : !source.enabled)));
  const supportsKey = (source: SourceConfig) => source.supports_api_key ?? ['openalex', 'semantic_scholar'].includes(source.id);
  const modelText = (key: 'model' | 'base_url', label: string) => <label className="form-field">{label}<input value={draft.model[key]} onChange={event => change(current => ({ ...current, model: { ...current.model, [key]: event.target.value } }))} /></label>;
  return <>
    <PageHeader title={currentSection.label} actions={<Button variant="primary" disabled={busy || !dirty} onClick={() => void save()}><Save size={14} />{busy ? '处理中…' : '保存设置'}</Button>} />
    {(error || resource.error) && <InlineError error={error || resource.error || ''} />}
    <div className="settings-content">
      <div className="stack">
        {dirty && <div className="notice warning compact">请先保存更改，再测试连接。</div>}
        {section === 'sources' && <section aria-label="数据源集成" className="integration-module">
          <div className="integration-toolbar"><SearchInput value={sourceSearch} onChange={setSourceSearch} label="搜索数据源" placeholder="搜索数据源…" /><Select aria-label="数据源启用筛选" value={sourceFilter} onValueChange={setSourceFilter} options={[{ value: 'all', label: '全部数据源' }, { value: 'enabled', label: '已启用' }, { value: 'disabled', label: '未启用' }]} /><span className="integration-count">{draft.sources.filter(source => source.enabled).length} / {draft.sources.length} 已启用</span></div>
          <div className="table-wrap integration-table-wrap" role="region" aria-label="数据源配置表格" tabIndex={0}>
            <table className="integration-table"><caption className="sr-only">数据源配置</caption>
              <thead><tr><th scope="col">数据源</th><th scope="col">启用</th><th scope="col">连接状态</th><th scope="col">密钥</th><th scope="col" className="integration-actions-heading">操作</th></tr></thead>
              <tbody>{sources.map(source => {
                const status = testedSources[source.id] || source.status;
                const keyStatus = source.api_key === '' ? '保存后清除' : source.api_key ? '待保存' : source.has_api_key ? '已配置' : source.requires_key ? '需要密钥' : '未设置';
                return <tr key={source.id} aria-label={providerNames[source.id] || source.name}>
                  <th scope="row">{providerNames[source.id] || source.name}</th>
                  <td><Switch aria-label={`启用 ${source.name}`} disabled={!source.implemented || busy} checked={source.enabled} onCheckedChange={checked => change(current => ({ ...current, sources: current.sources.map(item => item.id === source.id ? { ...item, enabled: checked } : item) }))} /></td>
                  <td><Badge tone={status === 'available' ? 'success' : status === 'unavailable' ? 'danger' : 'neutral'}>{statusLabels[status] || status}</Badge></td>
                  <td className={source.requires_key && !source.has_api_key ? 'key-required' : 'muted'}>{supportsKey(source) ? keyStatus : '—'}</td>
                  <td><div className="integration-row-actions"><Button disabled={busy || dirty || !source.implemented} onClick={() => setConfirmation({ kind: 'source', id: source.id, name: source.name })}><PlugZap size={14} />测试连接</Button><Button disabled={busy || !source.implemented} aria-label={`配置 ${source.name}`} onClick={() => setSourceEditor({ ...source })}><SlidersHorizontal size={14} />配置</Button></div></td>
                </tr>;
              })}</tbody>
            </table>{!sources.length && <EmptyState title="没有匹配的数据源" />}
          </div>
        </section>}
        {section === 'model' && <section className="settings-form-module" aria-labelledby="model-heading">
          <div className="settings-module-heading"><div><h2 id="model-heading">模型连接</h2><p>仅连接测试，核查流程尚未接入。</p></div><Switch aria-label="启用模型测试" checked={draft.model.enabled} onCheckedChange={checked => change(current => ({ ...current, model: { ...current.model, enabled: checked } }))} /></div>
          <div className="settings-form-fields">
            <div className="form-field"><label htmlFor="model-protocol">协议</label><Select id="model-protocol" aria-label="协议" value={draft.model.protocol} onValueChange={value => change(current => ({ ...current, model: { ...current.model, protocol: value } }))} options={[{ value: 'openai-chat', label: 'OpenAI Chat Completions' }, { value: 'openai-responses', label: 'OpenAI Responses' }, { value: 'ant-messages', label: 'Anthropic Messages' }]} /></div>
            {modelText('model', '模型 ID')}{modelText('base_url', 'Base URL')}
            <div className="settings-key-field"><label className="form-field">API key<input type="password" autoComplete="new-password" value={draft.model.api_key ?? ''} placeholder="留空不更换" onChange={event => change(current => ({ ...current, model: { ...current.model, api_key: event.target.value || undefined } }))} /></label><div className="settings-inline-meta"><span>{draft.model.api_key === '' ? '保存后清除' : draft.model.has_api_key ? '已保存' : '未设置'}</span>{draft.model.has_api_key && <Button variant="ghost" onClick={() => change(current => ({ ...current, model: { ...current.model, api_key: '' } }))}>清除密钥</Button>}</div></div>
            <div className="settings-number-row">{(['timeout_seconds', 'max_retries', 'max_output_tokens'] as const).map((key, index) => <label key={key} className="form-field">{['超时（秒）', '重试次数', '最大输出 tokens'][index]}<input type="number" min={key === 'max_retries' ? 0 : 1} value={draft.model[key]} onChange={event => change(current => ({ ...current, model: { ...current.model, [key]: Number(event.target.value) } }))} /></label>)}</div>
            <div className="settings-price"><span>价格来源</span><strong>MMSP 公开目录</strong>{draft.model.price != null ? <details><summary>价格快照</summary><pre>{JSON.stringify(draft.model.price, null, 2)}</pre></details> : <span className="muted">未匹配，费用未知</span>}</div>
          </div><div className="settings-form-footer"><Button disabled={busy || dirty || !draft.model.enabled || !draft.model.model} onClick={() => setConfirmation({ kind: 'model' })}>测试模型连接（可能计费）</Button></div>
        </section>}
        {section === 'runtime' && <section className="settings-form-module" aria-labelledby="runtime-heading"><div className="settings-module-heading"><h2 id="runtime-heading">新任务默认配置</h2></div><div className="settings-form-fields">
          {numeric('runtime', 'request_budget', '请求次数上限', 1, 5000)}
          <div className="settings-number-row">{numeric('runtime', 'timeout_seconds', '来源超时（秒）', 1, 60)}{numeric('runtime', 'max_retries', '来源重试次数', 0, 2)}</div>
          {numeric('runtime', 'concurrency', '并发数（当前固定）', 1, 1, true)}
          {numeric('runtime', 'max_upload_bytes', '上传上限（字节）', 1024, 20000000)}
        </div></section>}
        {section === 'cache' && <section className="settings-form-module" aria-labelledby="cache-heading"><div className="settings-module-heading"><h2 id="cache-heading">缓存有效期</h2></div><div className="settings-form-fields">
          {numeric('cache', 'positive_ttl_seconds', '成功结果有效期（秒）', 0, 604800)}
          {numeric('cache', 'negative_ttl_seconds', '未命中结果有效期（秒，当前固定）', 300, 300, true)}
          {numeric('cache', 'discovery_ttl_seconds', '版本发现有效期（秒）', 0, 2592000)}
        </div><div className="settings-form-footer"><Button disabled={busy} onClick={() => setConfirmation({ kind: 'cache' })}><Trash2 size={14} />清除查询缓存</Button></div></section>}
        {section === 'audit' && <section className="settings-audit-module"><div className="settings-module-heading"><h2>调用审计</h2></div><div className="settings-audit-options"><label><Checkbox aria-label="保存调用审计（始终开启）" disabled checked={draft.audit.enabled} onCheckedChange={() => {}} />保存调用审计（始终开启）</label><label><Checkbox aria-label="保存提示文本（当前不保留）" disabled checked={draft.audit.retain_prompt_text} onCheckedChange={() => {}} />保存提示文本（当前不保留）</label></div><div className="toolbar"><Select aria-label="调用来源筛选" value={provider} onValueChange={value => { setProvider(value); setCallPage(1); }} options={[{ value: '', label: '全部来源' }, ...draft.sources.map(source => ({ value: source.id, label: source.name }))]} /><Select aria-label="调用状态筛选" value={callStatus} onValueChange={value => { setCallStatus(value); setCallPage(1); }} options={[{ value: '', label: '全部状态' }, { value: 'success', label: '成功' }, { value: 'failed', label: '失败' }, { value: 'rate_limited', label: '限流' }, { value: 'no_result', label: '未命中' }]} /><Button onClick={calls.refresh}>刷新记录</Button></div>{calls.error && <InlineError error={calls.error} retry={calls.refresh} />}<section className="panel">{calls.loading && !calls.data ? <Loading /> : <CallTable calls={calls.data?.items ?? []} />}</section>{calls.data && <Pagination page={callPage} total={calls.data.total} size={20} onPage={setCallPage} />}</section>}
      </div>
    </div>
    {sourceEditor && <Modal title={`配置 ${providerNames[sourceEditor.id] || sourceEditor.name}`} onClose={() => setSourceEditor(null)}><div className="modal-body settings-source-editor"><div className="settings-config-toggle"><label htmlFor="source-enabled">启用数据源</label><Switch id="source-enabled" aria-label={`启用 ${sourceEditor.name}`} checked={sourceEditor.enabled} onCheckedChange={checked => setSourceEditor(current => current ? { ...current, enabled: checked } : null)} /></div>{supportsKey(sourceEditor) ? <div className="settings-key-field"><label className="form-field">更换 {sourceEditor.name} 密钥<input type="password" autoComplete="new-password" placeholder="留空不更换" value={sourceEditor.api_key ?? ''} onChange={event => setSourceEditor(current => current ? { ...current, api_key: event.target.value || undefined } : null)} /></label><div className="settings-inline-meta"><span>{sourceEditor.api_key === '' ? '保存后清除' : sourceEditor.has_api_key ? 'API key 已保存' : sourceEditor.requires_key ? '需要 API key' : 'API key 可选'}</span>{sourceEditor.has_api_key && <Button variant="ghost" onClick={() => setSourceEditor(current => current ? { ...current, api_key: '' } : null)}>清除密钥</Button>}</div></div> : <div className="settings-config-readonly"><span>认证方式</span><strong>无需 API key</strong></div>}</div><div className="modal-actions"><Button onClick={() => setSourceEditor(null)}>取消</Button><Button variant="primary" onClick={() => { change(current => ({ ...current, sources: current.sources.map(source => source.id === sourceEditor.id ? sourceEditor : source) })); setSourceEditor(null); }}>应用更改</Button></div></Modal>}
    {confirmation && <Modal title={confirmation.kind === 'cache' ? '清除查询缓存？' : confirmation.kind === 'model' ? '发起真实模型请求？' : `测试 ${confirmation.name}？`} onClose={() => { if (!busy) setConfirmation(null); }}><div className="modal-body"><p>{confirmation.kind === 'cache' ? '仅清除可重新生成的查询缓存。文章、版本、来源快照和历史任务报告不删除。' : confirmation.kind === 'model' ? `将使用已保存的 ${draft.model.model} 配置发起真实 API 请求，可能产生费用。费用以提供方账单为准。` : '将向该数据源发起真实联网请求，并写入调用审计记录。'}</p></div><div className="modal-actions"><Button disabled={busy} onClick={() => setConfirmation(null)}>取消</Button><Button variant="primary" disabled={busy} onClick={() => void execute()}>{busy ? '处理中…' : confirmation.kind === 'cache' ? '确认清除' : '确认发起请求'}</Button></div></Modal>}
  </>;
}
