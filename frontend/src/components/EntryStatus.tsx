import { AlertTriangle, ArrowUpRight, CircleCheck, CircleHelp, CircleMinus, CircleX, Clock3, LoaderCircle } from 'lucide-react';
import { classificationLabels, entryClassification, executionLabels, identityLabels } from '../format';
import type { EntrySummary } from '../types';

export default function EntryStatus({ entry }: { entry: EntrySummary }) {
  const kind = entryClassification(entry);
  const hasUpgrade = entry.recommendation_status === 'upgrade_available' && Boolean(entry.recommended_version_id);
  let label = kind ? classificationLabels[kind] : executionLabels[entry.execution_status];
  let tone = 'neutral';
  let Icon = CircleHelp;
  let detail = '';
  if (entry.verification_supported === false || entry.entry_type.toLowerCase() === 'patent') {
    label = '不支持核验'; Icon = CircleMinus;
  } else if (entry.execution_status === 'failed') {
    label = '检查失败'; tone = 'danger'; Icon = CircleX;
  } else if (['queued', 'running', 'cancelled'].includes(entry.execution_status)) {
    label = executionLabels[entry.execution_status];
    Icon = entry.execution_status === 'running' ? LoaderCircle : entry.execution_status === 'queued' ? Clock3 : CircleMinus;
  } else if (kind === 'wrong_paper') {
    tone = 'danger'; Icon = CircleX; detail = '标识指向另一篇论文';
  } else if (entry.required_corrections?.length) {
    label = '关键字段须修改'; tone = 'danger'; Icon = CircleX;
    detail = entry.required_corrections.join('、');
  } else if (entry.has_conflict) {
    label = '存在字段冲突'; tone = 'warning'; Icon = AlertTriangle;
    detail = kind ? classificationLabels[kind] : '';
  } else if (hasUpgrade) {
    label = '找到新版本'; tone = 'upgrade'; Icon = ArrowUpRight;
    detail = kind === 'version_confirmed' ? '当前版本已确认' : '当前版本未确认';
  } else if (kind === 'version_confirmed') {
    tone = entry.mode === 'demo' ? 'neutral' : 'success'; Icon = CircleCheck;
  } else if (kind === 'budget_exhausted' || kind === 'source_failed') {
    tone = 'warning'; Icon = AlertTriangle;
  } else if (kind === 'not_found') {
    tone = 'danger'; Icon = CircleX;
  } else {
    detail = identityLabels[entry.identity_state] ?? '';
  }
  if (entry.mode === 'demo') detail = ['演示数据', detail].filter(Boolean).join(' · ');
  return <td className={`entry-status-cell entry-status-${tone}`}>
    <div className="entry-status"><Icon size={18} aria-hidden="true" /><div><span className="entry-status-label">{label}</span>{detail && <small>{detail}</small>}{['存在字段冲突', '关键字段须修改'].includes(label) && hasUpgrade && <small className="entry-upgrade-note">找到新版本</small>}</div></div>
  </td>;
}
