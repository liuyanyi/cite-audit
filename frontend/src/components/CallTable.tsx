import { useState } from 'react';
import { Eye } from 'lucide-react';
import type { CallRecord } from '../types';
import { callStatus, costText, dateTime, providerNames } from '../format';
import { Badge, Button, EmptyState } from './ui';
import CallDialog from './CallDialog';
export default function CallTable({ calls }: { calls: CallRecord[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  if (!calls.length) return <EmptyState title="暂无调用记录">尚未发起来源请求，或当前条目无需新增请求。</EmptyState>;
  return <><div className="panel-table"><table className="audit-table"><caption className="sr-only">来源与模型调用审计记录</caption><thead><tr><th>来源 / 操作</th><th>状态</th><th>时间</th><th>耗时</th><th>尝试</th><th>缓存</th><th>费用</th><th>说明</th><th className="call-actions">操作</th></tr></thead><tbody>{calls.map(call => <tr key={call.id}><td><strong>{providerNames[call.provider] ?? call.provider}</strong><div className="row-subtitle">{call.operation} · {call.mode === 'demo' ? '演示' : '在线'}</div></td><td><Badge tone={call.status === 'success' ? 'success' : ['failed', 'rate_limited'].includes(call.status) ? 'danger' : 'warning'}>{callStatus[call.status] ?? call.status}</Badge></td><td className="nowrap muted">{dateTime(call.started_at)}</td><td>{call.duration_ms} ms</td><td>{call.attempt}</td><td>{call.cache_hit ? '命中' : '—'}</td><td className="nowrap">{costText(call.cost)}</td><td className="muted">{call.message ?? call.error_code ?? '—'}</td><td className="call-actions"><Button aria-label={`查看 ${providerNames[call.provider] ?? call.provider} ${call.operation} 调用详情`} onClick={() => setSelected(call.id)}><Eye size={14} />详情</Button></td></tr>)}</tbody></table></div>{selected && <CallDialog key={selected} id={selected} onClose={() => setSelected(null)} />}</>;
}
