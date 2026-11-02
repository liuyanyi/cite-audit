import type { Candidate } from '../types';
import { providerNames, safeExternal } from '../format';

export default function CandidateRecords({ candidates, excluded = [] }: { candidates: Candidate[]; excluded?: Candidate[] }) {
  const list = (items: Candidate[]) => items.map((candidate, i) => <div className="notice" key={i}><div>
    <strong>{candidate.title}</strong><p>{candidate.reason}</p>
    {candidate.author_match && <p className="small muted">作者：{{ full: '完整姓名及顺序匹配', partial: '缩写或列表不完整', order_variant: '多位作者姓/名字段可能倒置', different: '不一致', missing: '信息缺失' }[candidate.author_match] ?? candidate.author_match}；题名相似度：{candidate.title_similarity == null ? '未知' : `${Math.round(candidate.title_similarity * 100)}%`}（仅用于筛选，不是身份置信度）</p>}
    {(candidate.records ?? [candidate]).map((record, j) => <div key={j}> {safeExternal(record.url) && <a href={safeExternal(record.url)} target="_blank" rel="noopener noreferrer">{providerNames[record.provider] ?? record.provider}</a>} <span>{record.record_id}</span>
      {record.snapshot_id && <> · <a href={`/api/snapshots/${encodeURIComponent(record.snapshot_id)}`} target="_blank" rel="noopener noreferrer">来源快照</a></>}</div>)}
  </div></div>);
  return <><details className="expand-row"><summary>候选记录（{candidates.length}）</summary>
    {candidates.length ? list(candidates) : <p className="muted small">没有额外的相关候选。</p>}
  </details>{excluded.length > 0 && <details className="expand-row"><summary>已排除的检索结果（{excluded.length}）</summary>
    <p className="small muted">这些记录不参与身份歧义判断，保留供追溯。</p>{list(excluded)}
  </details>}</>;
}
