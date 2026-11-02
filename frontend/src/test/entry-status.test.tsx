import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import EntryStatus from '../components/EntryStatus';
import type { EntrySummary } from '../types';

const entry: EntrySummary = {
  id: 'entry', task_id: 'task', ordinal: 0, citation_key: 'preprint', entry_type: 'misc',
  title: 'Preprint', author: '', year: '2024', execution_status: 'completed',
  identity_state: 'confirmed', version_status: 'current_version_confirmed',
  recommendation_status: 'upgrade_available', recommended_version_id: 'formal',
  current_version_id: 'preprint', article_id: 'article', mode: 'online', diagnostics: [],
  has_conflict: false, conflict_count: 0, completion_count: 0,
};
const show = (changes: Partial<EntrySummary> = {}) => render(<table><tbody><tr><EntryStatus entry={{ ...entry, ...changes }} /></tr></tbody></table>);

it('shows a reliable upgrade in orange while retaining current-version confirmation', () => {
  show();
  expect(screen.getByText('找到新版本').closest('td')).toHaveClass('entry-status-upgrade');
  expect(screen.getByText('当前版本已确认')).toBeVisible();
});
it('does not imply that finding a new version verifies the current input', () => {
  show({ identity_state: 'insufficient', version_status: 'unknown' });
  expect(screen.getByText('当前版本未确认')).toBeVisible();
});
it('keeps conflicts primary and also exposes the upgrade', () => {
  show({ has_conflict: true });
  expect(screen.getByText('存在字段冲突').closest('td')).toHaveClass('entry-status-warning');
  expect(screen.getByText('找到新版本')).toHaveClass('entry-upgrade-note');
});
it('shows a wrong-paper identifier as a red entry error before field conflicts', () => {
  show({ identity_state: 'wrong_paper', has_conflict: true });
  expect(screen.getByText('论文错误').closest('td')).toHaveClass('entry-status-danger');
});
it.each(['same_version', 'unavailable', 'ambiguous'] as const)('does not promote %s recommendations', recommendation_status => {
  show({ recommendation_status });
  expect(screen.queryByText('找到新版本')).not.toBeInTheDocument();
});
it('requires an actual recommended version', () => {
  show({ recommended_version_id: null });
  expect(screen.queryByText('找到新版本')).not.toBeInTheDocument();
});
it('labels demo upgrades explicitly', () => {
  show({ mode: 'demo' });
  expect(screen.getByText('演示数据 · 当前版本已确认')).toBeVisible();
});
it.each(['queued', 'running', 'failed', 'cancelled'] as const)('keeps %s execution state primary', execution_status => {
  show({ execution_status });
  expect(screen.queryByText('找到新版本')).not.toBeInTheDocument();
});
