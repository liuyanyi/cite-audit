import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import CandidateRecords from '../components/CandidateRecords';

describe('candidate provenance and exclusions', () => {
  it('separates rejected noise from relevant candidates and exposes snapshot evidence', () => {
    render(<CandidateRecords candidates={[{ title: 'Relevant paper', provider: 'crossref', record_id: '1',
      url: 'https://doi.org/10.1234/example', reason: '需要核实年份', status: 'unresolved',
      author_match: 'partial', title_similarity: 0.98, snapshot_id: 'snap1' }]}
      excluded={[{ title: 'Unrelated paper', provider: 'openalex', record_id: '2',
        url: 'javascript:alert(1)', reason: '标题明显不匹配', status: 'rejected' }]} />);
    expect(screen.getByText('候选记录（1）')).toBeInTheDocument();
    expect(screen.getByText('已排除的检索结果（1）')).toBeInTheDocument();
    expect(screen.getByText(/缩写或列表不完整/)).toBeInTheDocument();
    expect(screen.getByText('来源快照')).toHaveAttribute('href', '/api/snapshots/snap1');
    expect(screen.queryByRole('link', { name: 'OpenAlex', hidden: true })).toBeNull();
  });
});
