import { Badge } from './components/ui';
import type { Cost, EntryClassification, EntrySummary, Execution, Mode, RecommendationState } from './types';
export const executionLabels: Record<Execution, string> = { queued: '排队中', running: '核查中', completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消' };
export const identityLabels: Record<string, string> = { wrong_paper: '论文错误', confirmed: '身份已确认', ambiguous: '身份有歧义', not_found: '未找到匹配', insufficient: '证据不足', source_failed: '未找到匹配' };
export const recommendationLabels: Record<RecommendationState, string> = { same_version: '当前为推荐版本', upgrade_available: '建议正式版', unavailable: '暂无可靠建议', ambiguous: '推荐待确认' };
export const versionLabels: Record<string, string> = { preprint: '预印本', conference: '会议发表版', journal: '期刊发表版', other: '其他版本' };
export const judgmentLabels: Record<string, string> = { consistent: '一致', conflict: '明确冲突', completion: '可补全', normalization: '等价规范化', unverifiable: '未核实', not_applicable: '不适用', unchanged: '保持', version_change: '版本切换需更新', target_unverified: '目标版本待核' };
export const providerNames: Record<string, string> = { iclr: 'ICLR Proceedings', crossref: 'Crossref', arxiv: 'arXiv', openalex: 'OpenAlex', semantic_scholar: 'Semantic Scholar', acl: 'ACL Anthology', acl_anthology: 'ACL Anthology', pmlr: 'PMLR', tmlr: 'TMLR', openreview: 'OpenReview', fixture: '离线演示来源', demo: '离线演示来源' };
export const sourceTypes: Record<string, string> = { official: '官方出版记录', registry: '注册机构', preprint: '预印本平台', aggregator: '聚合平台', manual: '人工未核验', demo: '离线样例' };
export const sourceStatuses: Record<string, string> = { available: '已获取', stale: '需复核', inaccessible: '当前不可访问', failed: '获取失败', demo: '演示数据', manual_unverified: '人工未核验' };
export const callStatus: Record<string, string> = { success: '成功', no_result: '未命中', failed: '失败', rate_limited: '限流', budget_exhausted: '预算耗尽', cancelled: '已取消', partial: '部分完成' };
export const classificationLabels: Record<EntryClassification, string> = { wrong_paper: '论文错误', version_confirmed: '版本已确认', not_found: '未找到匹配', unverifiable: '无法核验' };
export function entryClassification(entry: EntrySummary): EntryClassification | null {
  // Derive the display group from the entry's own states, including frozen
  // reports; a saved presentation label cannot override the original outcome.
  if (['queued', 'running'].includes(entry.execution_status)) return null;
  if (['failed', 'cancelled'].includes(entry.execution_status) || entry.verification_supported === false || entry.entry_type.toLowerCase() === 'patent') return 'unverifiable';
  if (entry.identity_state === 'wrong_paper') return 'wrong_paper';
  if (entry.identity_state === 'confirmed' && entry.version_status === 'current_version_confirmed') return 'version_confirmed';
  return ['not_found', 'source_failed'].includes(entry.identity_state) ? 'not_found' : 'unverifiable';
}
export function ClassificationBadge({ entry }: { entry: EntrySummary }) {
  const kind = entryClassification(entry);
  return kind ? <Badge tone={kind === 'wrong_paper' || kind === 'not_found' ? 'danger' : kind === 'version_confirmed' && entry.mode === 'online' ? 'success' : 'neutral'}>{classificationLabels[kind]}</Badge> : <span className="muted">—</span>;
}
export function ExecutionBadge({ status }: { status: Execution }) { return <Badge tone={status === 'completed' ? 'success' : status === 'running' ? 'info' : status === 'failed' ? 'danger' : status === 'partial' ? 'warning' : 'neutral'}>{executionLabels[status] ?? status}</Badge>; }
export function ModeBadge({ mode }: { mode: Mode }) { return <Badge tone={mode === 'demo' ? 'warning' : 'neutral'}>{mode === 'demo' ? '演示数据' : '在线核查'}</Badge>; }
export function FactBadge({ entry }: { entry: EntrySummary }) { if (entry.verification_supported === false || entry.entry_type.toLowerCase() === 'patent') return <Badge tone="neutral">不支持核验</Badge>; if (entry.execution_status === 'failed') return <Badge tone="danger">检查失败</Badge>; if (['queued', 'running', 'cancelled'].includes(entry.execution_status)) return <ExecutionBadge status={entry.execution_status} />; if (entry.identity_state === 'wrong_paper') return <Badge tone="danger">论文错误</Badge>; if (entry.has_conflict) return <Badge tone={conflictTone(entry.version_status)}>存在字段冲突</Badge>; if (entry.identity_state === 'confirmed') return <Badge tone={entry.mode === 'demo' ? 'neutral' : 'success'}>{entry.mode === 'demo' ? '样例身份匹配' : '当前版本身份已确认'}</Badge>; return <Badge tone={['not_found', 'source_failed'].includes(entry.identity_state) ? 'danger' : 'warning'}>{identityLabels[entry.identity_state] ?? entry.identity_state}</Badge>; }
export function RecommendationBadge({ status }: { status: RecommendationState }) { return <Badge tone={status === 'upgrade_available' ? 'info' : 'neutral'}>{recommendationLabels[status] ?? status}</Badge>; }
const conflictTone = (versionStatus?: string) => versionStatus === 'current_version_confirmed' ? 'conflict' : 'danger';
export function JudgmentBadge({ judgment, versionStatus }: { judgment: string; versionStatus?: string }) { return <Badge tone={judgment === 'conflict' ? conflictTone(versionStatus) : judgment === 'consistent' ? 'success' : judgment === 'completion' || judgment === 'unverifiable' || judgment === 'target_unverified' ? 'warning' : judgment === 'version_change' ? 'info' : 'neutral'}>{judgmentLabels[judgment] ?? judgment}</Badge>; }
export function dateTime(value?: string | null) { if (!value) return '—'; const date = new Date(value); return isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date); }
export function costText(cost?: Cost | null) { return cost?.known && cost.amount != null && cost.currency ? `${cost.currency} ${cost.amount.toFixed(5)}` : '未知'; }
export function bytes(value: number) { return value < 1024 ? `${value} B` : value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`; }
export function safeExternal(url?: string | null) { if (!url) return undefined; try { const parsed = new URL(url); return ['https:', 'http:'].includes(parsed.protocol) ? parsed.href : undefined; } catch { return undefined; } }
export function queryString(values: Record<string, string | number | boolean | null | undefined>) { const params = new URLSearchParams(); Object.entries(values).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') params.set(k, String(v)); }); return params.toString(); }
export const firstAuthor = (authors: string) => authors?.split(/\s+and\s+/i)[0] || '—';
