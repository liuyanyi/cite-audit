import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import App from '../App';
import { settingsFixture } from './fixtures';
import { ClassificationBadge, FactBadge, costText, safeExternal } from '../format';
import { request } from '../api';
import { SourceCard } from '../components/EvidencePanel';
import RetryDialog from '../components/RetryDialog';
import { ToastProvider } from '../components/ui';
import type { EntryDetail, SourceContent, Task } from '../types';
const summary = { total: 1, completed: 1, running: 0, queued: 0, failed: 0, cancelled: 0, parse_errors: 0, confirmed: 1, conflicts: 0, unresolved: 0, upgrade_available: 1, completion_available: 0, problem_entries: 0, version_confirmed: 1, not_found: 0, unverifiable: 0, unprocessed: 0 };
const task: Task = { id: 't1', name: '论文引用', filename: 'paper.bib', mode: 'demo', original_sha256: 'original', byte_size: 200, encoding: 'utf-8', created_at: '2026-10-30T05:00:00Z', updated_at: '2026-10-30T05:00:00Z', execution_status: 'completed', error: null, config: {}, summary, warnings: [], cost: { known: false, amount: null, currency: null }, original_url: '/api/tasks/t1/original', report_url: '/api/tasks/t1/report' };
const source: SourceContent = { id: 's1', provider: 'fixture', record_id: 'synthetic', object_type: 'publication', version_id: 'v1', attribution_status: 'confirmed', source_type: 'demo', mode: 'demo', url: 'javascript:alert(1)', fetched_at: task.created_at, checked_at: task.created_at, status: 'demo', snapshot_id: 'snap1', snapshot_sha256: 'hash', fields: { year: '2024' }, field_coverage: ['year'], extraction_method: 'fixture', snapshot_url: '/api/snapshots/snap1', warnings: [] };
const entry: EntryDetail = { id: 'e1', task_id: 't1', ordinal: 0, citation_key: 'sample2024', entry_type: 'misc', title: 'Synthetic Paper', author: 'Example, Ada', year: '2024', execution_status: 'completed', identity_state: 'confirmed', version_status: 'current_version_confirmed', recommendation_status: 'upgrade_available', has_conflict: false, conflict_count: 0, completion_count: 0, article_id: 'a1', current_version_id: 'v1', recommended_version_id: 'v2', mode: 'demo', diagnostics: [], original_bib: '@misc{sample2024,year={2024}}', fields: { year: '2024' }, identity_reason: '明确匹配当前预印本', field_assessments: [{ field: 'year', original_value: '2024', reference_value: '2024', judgment: 'consistent', reason: '当前版本一致', evidence: [{ source_content_id: 's1', snapshot_id: 'snap1', field: 'year', locator: 'fields.year' }] }], recommendation: { status: 'upgrade_available', version_id: 'v2', reason: '存在已关联正式版', revision: 1, differences: [{ field: 'year', original_value: '2024', reference_value: '2025', judgment: 'version_change', evidence: [] }] }, current_version: null, recommended_version: null, version_relations: [], proposals: [{ id: 'p1', proposal_type: 'whole_version_switch', target_version_id: 'v2', fields: { year: '2025' }, remove_fields: [], bibtex: '@article{sample2024,year={2025}}', reason: '已确认关联的正式版', evidence: [], safe: true, blocked_reason: null }], sources: [source], candidates: [], calls: [], warnings: [], evidence_revision: { merge_revision: 1, recommendation_revision: 1, rule_version: '1' } };
const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 10 });
let fetchMock: ReturnType<typeof vi.fn<(url: string, options?: RequestInit) => Promise<Response>>>;
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
beforeEach(() => { localStorage.clear(); fetchMock = vi.fn(async (url: string, opts?: RequestInit) => { const path = url.split('?')[0]; if (path === '/api/settings') return reply(settingsFixture); if (opts?.method === 'DELETE') return reply({ deleted: true }); if (path === '/api/tasks') return reply(page([task])); if (path === '/api/tasks/t1') return reply(task); if (path === '/api/tasks/t1/entries') return reply(page([entry])); if (path === '/api/tasks/t1/entries/e1') return reply(entry); if (path === '/api/tasks/t1/report') return reply({ task, entries: [entry], calls: [] }); if (path === '/api/articles') return reply(page([])); if (path === '/api/snapshots/snap1') return reply({ body: '<script>alert(1)</script>', sha256: 'hash' }); return reply({}); }); vi.stubGlobal('fetch', fetchMock); Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => vi.unstubAllGlobals());
const mount = (path = '/tasks') => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
describe('navigation and truth states', () => {
  it('retains manual retry selections across pages', async () => {
    fetchMock.mockImplementation(async url => {
      if (url.endsWith('/retry')) return reply(task);
      const second = new URL(url, 'http://localhost').searchParams.get('page') === '2';
      return reply({ ...page([{ ...entry, id: second ? 'e2' : 'e1', title: second ? 'Second paper' : 'First paper' }]), total: 11 });
    });
    render(<RetryDialog task={task} classification="" onClose={() => {}} onStarted={() => {}} />);
    const dialog = within(screen.getByRole('dialog'));
    await userEvent.click(dialog.getByRole('tab', { name: '手动选择条目' }));
    await userEvent.click(await dialog.findByRole('checkbox', { name: /First paper/ }));
    await userEvent.click(dialog.getByRole('button', { name: '下一页' }));
    await userEvent.click(await dialog.findByRole('checkbox', { name: /Second paper/ }));
    await userEvent.click(dialog.getByRole('button', { name: '开始重跑 2 条' }));
    const call = fetchMock.mock.calls.find(([url]) => url.endsWith('/retry'));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ scope: 'entries', revision: 1, entry_ids: ['e1', 'e2'] });
  });
  it('renders readable journal text while preserving original Bib and copy values', async () => {
    const original = fetchMock.getMockImplementation()!;
    const journal = 'Theory \\&; Applications';
    const bib = `@article{sample2024,journal={${journal}}}`;
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) => url === '/api/tasks/t1/entries/e1' ? reply({ ...entry,
      original_bib: bib,
      field_assessments: [{ field: 'journal', original_value: journal, reference_value: 'Theory &amp; Applications', judgment: 'normalization', evidence: [] }],
    }) : original(url, options));
    const user = userEvent.setup();
    mount('/tasks/t1/entries/e1');
    expect(await screen.findAllByText('Theory & Applications')).toHaveLength(2);
    expect(screen.getByText(bib)).toBeVisible();
    expect(screen.getByTitle(journal)).toBeVisible();
    await user.click(screen.getByRole('button', { name: '复制原 journal' }));
    expect(await navigator.clipboard.readText()).toBe(journal);
  });
  it('starts on file tasks rather than mixed citation results', async () => { mount(); expect(await screen.findByText('论文引用')).toBeVisible(); expect(screen.getByRole('heading', { name: 'Bib 核查任务' })).toBeVisible(); expect(screen.queryByText('Synthetic Paper')).not.toBeInTheDocument(); });
  it('separates valid preprint verification from a recommended version change', async () => { mount('/tasks/t1/entries/e1'); expect(await screen.findByText('当前版本一致')).toBeVisible(); expect(screen.queryByText('明确冲突')).not.toBeInTheDocument(); await userEvent.click(screen.getByRole('tab', { name: '推荐版本差异' })); expect(screen.getByText('版本切换需更新')).toBeVisible(); expect(screen.getByText('2025')).toBeVisible(); expect(screen.queryByText('明确冲突')).not.toBeInTheDocument(); });
  it('does not repeat the generic unconfirmed-publication reason in each row', async () => {
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) => url === '/api/tasks/t1/entries/e1' ? reply({ ...entry, version_status: 'unknown', identity_reason: 'Source unavailable', field_assessments: [{ field: 'year', original_value: '2024', reference_value: null, judgment: 'unverifiable', reason: 'Current publication is not confirmed', evidence: [] }] }) : original(url, options));
    mount('/tasks/t1/entries/e1'); expect(await screen.findByText('Source unavailable')).toBeVisible(); expect(screen.getByText('未核实')).toBeVisible(); expect(screen.queryByText('Current publication is not confirmed')).not.toBeInTheDocument();
  });
  it('locates source evidence and returns to a field', async () => { mount('/tasks/t1/entries/e1'); await screen.findByText('当前版本一致'); await userEvent.click(screen.getByRole('button', { name: '离线演示来源 1' })); expect(document.getElementById('source-s1')).toHaveClass('highlighted'); await userEvent.click(screen.getByRole('button', { name: 'year 2024' })); expect(document.getElementById('field-year')).toHaveClass('highlighted'); });
  it('preserves list filters in back navigation', async () => { mount('/tasks/t1/entries/e1?q=Synthetic&identity=confirmed'); await screen.findByText('当前版本一致'); expect(screen.getByRole('link', { name: '返回任务' })).toHaveAttribute('href', '/tasks/t1?q=Synthetic&identity=confirmed'); });
  it('distinguishes a processed partial task from a successful audit', async () => {
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) => url === '/api/tasks/t1' ? reply({ ...task, mode: 'online', execution_status: 'partial', summary: { ...summary, confirmed: 0, unresolved: 1 } }) : original(url, options));
    mount('/tasks/t1'); expect(await screen.findByText('已处理')).toBeVisible(); expect(screen.getByText('未处理的')).toBeVisible();
    expect(screen.queryByText('检查完成')).not.toBeInTheDocument();
    expect(screen.getByLabelText('已处理 100%').firstElementChild).toHaveClass('warning');
  });
  it('opens knowledge as an independent page', async () => { mount('/knowledge'); expect(await screen.findByText('暂无文章')).toBeVisible(); expect(screen.getByRole('heading', { name: '文献知识库' })).toBeVisible(); });
  it('switches theme without losing the page', async () => { mount(); await screen.findByText('论文引用'); await userEvent.click(screen.getByRole('button', { name: '切换深色模式' })); expect(document.documentElement.dataset.theme).toBe('dark'); expect(localStorage.getItem('cite-audit-theme')).toBe('dark'); });
});
describe('safe actions', () => {
  it('requires explicit confirmation before deleting only a task', async () => { mount(); await userEvent.click(await screen.findByRole('button', { name: '操作 论文引用' })); await userEvent.click(screen.getByRole('menuitem', { name: '删除任务' })); expect(fetchMock.mock.calls.some(c => c[1]?.method === 'DELETE')).toBe(false); await userEvent.click(screen.getByRole('button', { name: '取消' })); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(fetchMock.mock.calls.some(c => c[1]?.method === 'DELETE')).toBe(false); });
  it('closes upload on Escape and returns focus to trigger', async () => { mount(); await screen.findByText('论文引用'); const trigger = screen.getByRole('button', { name: '上传 Bib 文件' }); await userEvent.click(trigger); expect(screen.getByRole('dialog')).toBeVisible(); await userEvent.keyboard('{Escape}'); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(trigger).toHaveFocus(); });
  it('previews export with no proposals selected by default and requires confirmation', async () => { fetchMock.mockImplementation(async (url: string) => { if (url.endsWith('/export-preview')) return reply({ filename: 'new.bib', bibtex: entry.original_bib, original_sha256: 'original', output_sha256: 'original', changes: [], warnings: [], blocked: [], changed_entries: 0, unchanged_entries: 1 }); if (url.endsWith('/report')) return reply({ entries: [entry] }); if (url.split('?')[0].endsWith('/entries')) return reply(page([entry])); return reply(task); }); mount('/tasks/t1'); await userEvent.click(await screen.findByRole('button', { name: '校订预览 / 导出' })); expect(await screen.findByRole('radio', { name: '保留当前输入' })).toBeChecked(); await userEvent.click(screen.getByRole('button', { name: '预览 0 项选择' })); expect(await screen.findByRole('button', { name: '确认生成新文件' })).toBeDisabled(); const previewCall = fetchMock.mock.calls.find(c => c[0].endsWith('/export-preview')); expect(JSON.parse(previewCall![1]!.body as string)).toEqual({ selections: [] }); });
  it('retries within the current task and keeps the current route', async () => {
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, opts?: RequestInit) => { if (url === '/api/tasks/t1') return reply({ ...task, execution_status: 'failed' }); if (url.endsWith('/retry')) return reply({ ...task, execution_status: 'queued' }); return original(url, opts); });
    mount('/tasks/t1'); await userEvent.click(await screen.findByRole('button', { name: '重跑设置…' }));
    await userEvent.click(screen.getByRole('tab', { name: '全部条目' }));
    await userEvent.click(screen.getByRole('button', { name: /^开始重跑 \d+ 条$/ }));
    expect(screen.getByRole('heading', { name: '论文引用' })).toBeVisible();
    expect(await screen.findByText('已在当前任务中重新核查，历史报告已保留。')).toBeVisible();
    expect(fetchMock.mock.calls.filter(c => c[0].endsWith('/retry'))).toHaveLength(1);
    expect(fetchMock.mock.calls.some(c => c[0].includes('/tasks/t2'))).toBe(false);
  });
  it('submits online uploads with a notice and no extra consent checkbox', async () => {
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) => url === '/api/tasks' && options?.method === 'POST' ? reply(task) : original(url, options));
    mount(); await screen.findByText('论文引用'); await userEvent.click(screen.getByRole('button', { name: '上传 Bib 文件' }));
    const upload = screen.getByLabelText('完整 BibTeX 文件'); await userEvent.upload(upload, new File(['@misc{x}'], 'paper.bib', { type: 'application/x-bibtex' }));
    await userEvent.click(screen.getByRole('radio', { name: /在线核查/ }));
    const submit = screen.getByRole('button', { name: '开始在线核查' });
    expect(submit).toBeEnabled();
    expect(submit).toHaveAccessibleDescription('开始核查即同意向已启用的文献来源发送必要查询字段。');
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(c => c[1]?.method === 'POST')).toBe(false);
    await userEvent.click(submit);
    const call = fetchMock.mock.calls.find(c => c[0] === '/api/tasks' && c[1]?.method === 'POST');
    expect((call?.[1]?.body as FormData).get('mode')).toBe('online');
  });
  it('keeps whole-version export atomic and sends the reviewed hash', async () => {
    fetchMock.mockImplementation(async (url: string) => { if (url.endsWith('/export-preview')) return reply({ filename: 'new.bib', bibtex: '@article{x}', original_sha256: 'old', output_sha256: 'reviewed', changes: [], warnings: [], blocked: [], changed_entries: 1, unchanged_entries: 0 }); if (url.endsWith('/exports')) return reply({ filename: 'new.bib', original_sha256: 'old', output_sha256: 'reviewed', download_url: '/download', report_url: '/report' }); if (url.endsWith('/report')) return reply({ entries: [entry] }); if (url.split('?')[0].endsWith('/entries')) return reply(page([entry])); return reply(task); });
    mount('/tasks/t1'); await userEvent.click(await screen.findByRole('button', { name: '校订预览 / 导出' })); await userEvent.click(await screen.findByRole('radio', { name: /整体改用推荐版本/ }));
    expect(screen.queryByRole('checkbox', { name: 'year' })).not.toBeInTheDocument(); await userEvent.click(screen.getByRole('button', { name: '预览 1 项选择' })); await userEvent.click(await screen.findByRole('checkbox', { name: /我已审阅差异和未决项/ })); await userEvent.click(screen.getByRole('button', { name: '确认生成新文件' }));
    expect(await screen.findByRole('heading', { name: '校订文件已生成' })).toBeVisible(); const call = fetchMock.mock.calls.find(c => c[0].endsWith('/exports')); expect(JSON.parse(call![1]!.body as string)).toEqual({ selections: [{ entry_id: 'e1', proposal_id: 'p1', fields: null }], confirmed: true, expected_output_sha256: 'reviewed' });
  });
  it('renders untrusted snapshot text inertly and blocks unsafe source URLs', async () => { render(<ToastProvider><SourceCard source={source} index={0} /></ToastProvider>); expect(screen.queryByRole('link')).not.toBeInTheDocument(); await userEvent.click(screen.getByRole('button', { name: '原始快照' })); expect(await screen.findByText('<script>alert(1)</script>')).toBeVisible(); expect(document.querySelector('script')).toBeNull(); });
});
describe('API and formatting', () => {
  it.each([{ entry_type: 'patent' }, { entry_type: 'misc', verification_supported: false }])('shows unsupported entries in gray', overrides => {
    render(<FactBadge entry={{ ...entry, ...overrides }} />);
    expect(screen.getByText('不支持核验')).toHaveClass('neutral');
    expect(screen.queryByText('证据不足')).not.toBeInTheDocument();
  });
  it('marks an actual unmatched result red', () => {
    render(<FactBadge entry={{ ...entry, identity_state: 'not_found', has_conflict: false }} />);
    expect(screen.getByText('未找到匹配')).toHaveClass('danger');
  });
  it('retains the original red unmatched display for source failures in both result and type badges', () => {
    const failed = { ...entry, identity_state: 'source_failed' as const, version_status: 'unknown', has_conflict: false, classification: 'unverifiable' as const };
    render(<><FactBadge entry={failed} /><ClassificationBadge entry={failed} /></>);
    expect(screen.getAllByText('来源访问失败')).toHaveLength(2);
    expect(screen.queryByText('未找到匹配')).not.toBeInTheDocument();
    expect(screen.queryByText('无法核验')).not.toBeInTheDocument();
  });
  it('keeps unknown cost distinct from zero', () => { expect(costText({ known: false, amount: 0, currency: 'USD' })).toBe('未知'); expect(costText({ known: true, amount: 0, currency: 'USD' })).toBe('USD 0.00000'); });
  it('rejects executable source links', () => { expect(safeExternal('javascript:alert(1)')).toBeUndefined(); expect(safeExternal('data:text/html,x')).toBeUndefined(); expect(safeExternal('https://example.org')).toBe('https://example.org/'); });
  it('does not label synthetic evidence as online verified', () => { render(<FactBadge entry={entry} />); expect(screen.getByText('样例身份匹配')).toBeVisible(); expect(screen.queryByText('当前版本已核实')).not.toBeInTheDocument(); });
  it('surfaces structured server errors', async () => { fetchMock.mockResolvedValue(reply({ error: { message: '证据不足，不能导出' } }, 409)); await expect(request('/test')).rejects.toThrow('证据不足，不能导出'); });
  it('handles abort without falsely presenting a server failure', async () => { fetchMock.mockRejectedValue(new DOMException('Aborted', 'AbortError')); await expect(request('/test')).rejects.toHaveProperty('name', 'AbortError'); });
});
