import { useResource } from '../api';
import type { CallRecord } from '../types';
import { callStatus, costText, providerNames } from '../format';
import { Badge, Button, InlineError, Loading, Modal } from './ui';
import './calls.css';

function queryParameters(summary?: string) {
  try { return [...new URL(summary ?? '').searchParams.entries()]; }
  catch { return []; }
}

function responseText(body: string) {
  try { return JSON.stringify(JSON.parse(body), null, 2); }
  catch { return body; }
}

export default function CallDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: call, error, refresh } = useResource<CallRecord>(`/calls/${encodeURIComponent(id)}`);
  const response = call?.response_summary;
  const parameters = queryParameters(call?.request_summary);
  return <Modal title="调用详情" onClose={onClose} wide>
    <div className="modal-body call-detail">
      {error ? <InlineError error={error} retry={refresh} /> : !call ? <Loading /> : <>
        <div className="call-detail-heading"><h3>{providerNames[call.provider] ?? call.provider}<span>{call.operation}</span></h3><Badge tone={call.status === 'success' ? 'success' : ['failed', 'rate_limited'].includes(call.status) ? 'danger' : 'warning'}>{callStatus[call.status] ?? call.status}</Badge></div>
        {call.mode === 'demo' && <div className="notice warning">演示数据：离线固定样例，不代表在线搜索或核验。</div>}
        <dl className="call-meta">
          <div><dt>调用时间</dt><dd>{call.started_at}</dd></div><div><dt>耗时 / 尝试</dt><dd>{call.duration_ms} ms / 第 {call.attempt} 次</dd></div>
          <div><dt>缓存</dt><dd>{call.cache_hit ? '命中（复用已保存响应）' : call.request_count === 0 ? '无网络请求' : '未命中'}</dd></div><div><dt>费用</dt><dd>{costText(call.cost)}</dd></div>
          {call.http_status != null && <div><dt>HTTP 状态</dt><dd>{call.http_status}</dd></div>}
          {call.fetched_at && <div><dt>来源获取时间</dt><dd>{call.fetched_at}</dd></div>}
          {call.task_id && <div><dt>任务 / 报告修订</dt><dd>{call.task_id} / {call.report_revision ?? '未记录'}</dd></div>}
          {call.entry_id && <div><dt>条目</dt><dd>{call.entry_id}</dd></div>}
        </dl>
        {(call.message || call.error_code) && <section><h3>调用说明</h3><p>{call.message}</p>{call.error_code && <code>{call.error_code}</code>}</section>}
        <section><h3>搜索 / 请求内容</h3>{call.request_summary ? <>
          <pre className="call-payload">{responseText(call.request_summary)}</pre>
          {!!parameters.length && <dl className="call-parameters">{parameters.map(([key, value], index) => <div key={index}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>}
        </> : <p className="muted">此记录未保存请求内容。</p>}</section>
        <section><h3>返回结果{response?.record_count != null ? ` · ${response.record_count} 条` : call.record_count != null ? ` · ${call.record_count} 条` : ''}</h3>
          {response ? <>
            {response.records && <><p className="muted">来源返回的候选记录，尚不代表已确认匹配或可跨版本合并。</p>{response.records.length ? response.records.map((record, index) => <article className="call-result" key={index}>
              <h4>{record.fields.title || record.record_id}</h4><p className="muted">{record.provider} · {record.record_id}</p>
              <dl className="call-parameters">{Object.entries(record.fields).map(([field, value]) => <div key={field}><dt>{field}</dt><dd>{value}</dd></div>)}</dl>
              <pre className="call-payload">{JSON.stringify(record.identifiers, null, 2)}</pre>
            </article>) : <p>本次来源处理未返回候选记录。</p>}</>}
            {response.body !== undefined && <>
              <p className="muted">HTTP {response.http_status} · {response.byte_size} 字节{response.url && ` · ${response.url}`}</p>
              {response.truncated && <div className="notice warning">响应较长，仅保存前 32,000 个字符；此预览不是完整响应。</div>}
              <pre className="call-payload" aria-label="来源响应内容">{response.body ? responseText(response.body) : '响应正文为空。'}</pre>
            </>}
          </> : <p className="muted">此记录未保存返回内容，无法还原当时的搜索结果。</p>}
        </section>
        {call.usage && <section><h3>模型用量</h3><pre className="call-payload">{JSON.stringify(call.usage, null, 2)}</pre></section>}
      </>}
    </div>
    <div className="modal-actions"><Button onClick={onClose}>关闭</Button></div>
  </Modal>;
}
