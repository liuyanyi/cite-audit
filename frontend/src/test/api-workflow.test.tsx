import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import App from '../App';
import { UploadDialog } from '../components/UploadDialog';
import type { EntrySummary, ExportArtifact, Page, Settings, Task } from '../types';
import { ApiEvents } from './sse-client';

// Real local API and SQLite; every uploaded task explicitly uses offline demo mode.
const root = resolve(process.cwd(), '..');
const nativeFetch = globalThis.fetch;
let server: ChildProcess | undefined;
let directory = '';
let base = '';
let logs = '';

beforeAll(async () => {
  directory = await mkdtemp(join(tmpdir(), 'cite-audit-ui-'));
  const listener = createServer();
  await new Promise<void>(done => listener.listen(0, '127.0.0.1', done));
  const address = listener.address();
  if (!address || typeof address === 'string') throw new Error('No local test port');
  const port = address.port;
  await new Promise<void>((done, reject) => listener.close(error => error ? reject(error) : done()));
  base = `http://127.0.0.1:${port}`;
  const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const script = `
import asyncio, uvicorn
from cite_audit.api import create_app
from cite_audit.sources import SourceRegistry
class OfflineRegistry(SourceRegistry):
    async def lookup(self, *args, **kwargs):
        await asyncio.sleep(0.05)
        return await super().lookup(*args, **kwargs)
uvicorn.run(create_app(data_dir=${JSON.stringify(directory)}, registry=OfflineRegistry()), host='127.0.0.1', port=${port})
`;
  server = spawn(python, ['-B', '-c', script], {
    cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stdout?.on('data', data => { logs = (logs + data).slice(-4000); });
  server.stderr?.on('data', data => { logs = (logs + data).slice(-4000); });
  let failure: Error | undefined;
  server.on('error', error => { failure = error; });
  for (let attempt = 0; attempt < 150; attempt++) {
    if (failure) throw failure;
    if (server.exitCode !== null) throw new Error(`Local test server exited: ${logs}`);
    try { if ((await nativeFetch(`${base}/api/health`)).ok) return; } catch { /* Wait for local startup. */ }
    await new Promise(done => setTimeout(done, 100));
  }
  throw new Error(`Local test server did not start: ${logs}`);
}, 30000);

afterEach(() => { ApiEvents.instances.forEach(stream => stream.close()); ApiEvents.instances = []; vi.unstubAllGlobals(); });
afterAll(async () => {
  if (server && server.exitCode === null && server.pid) {
    const exited = new Promise(done => server!.once('exit', done));
    // Kill only the test process tree, including uv's Windows Python trampoline.
    if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(server.pid), '/t', '/f'], { windowsHide: true });
    else server.kill();
    await exited;
  }
  const target = resolve(directory);
  if (directory && target.startsWith(resolve(tmpdir()) + (process.platform === 'win32' ? '\\' : '/'))
      && target.split(/[\\/]/).at(-1)?.startsWith('cite-audit-ui-')) {
    await rm(target, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  }
});

function connect(onExport?: (artifact: ExportArtifact) => void) {
  vi.stubGlobal('fetch', async (input: string, options?: RequestInit) => {
    const response = await nativeFetch(new URL(input, base), options);
    if (options?.method === 'POST' && input.endsWith('/exports') && response.ok) {
      onExport?.(await response.clone().json() as ExportArtifact);
    }
    return response;
  });
}

it('opens immutable source-call results from audit row actions through the real API', async () => {
  const boundary = 'call-details-ui';
  const raw = '@article{audit,eprint={1810.04805}}';
  const body = `--${boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\ndemo\r\n--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="audit.bib"\r\n\r\n${raw}\r\n--${boundary}--\r\n`;
  const task = await (await nativeFetch(`${base}/api/tasks`, { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body })).json() as Task;
  await nativeFetch(`${base}/api/events?task_id=${task.id}`).then(response => response.text());
  const report = await (await nativeFetch(`${base}/api/tasks/${task.id}/report`)).text();
  const requests: string[] = [];
  vi.stubGlobal('fetch', (input: string, options?: RequestInit) => {
    requests.push(input);
    return nativeFetch(new URL(input, base), options);
  });
  render(<MemoryRouter initialEntries={['/settings/audit']}><App /></MemoryRouter>);
  const table = await screen.findByRole('table', { name: '来源与模型调用审计记录' });
  const buttons = within(table).getAllByRole('button', { name: /调用详情/ });
  expect(requests.some(path => /\/calls\/[^?]/.test(path))).toBe(false);
  await userEvent.click(buttons[0]);
  const dialog = await screen.findByRole('dialog', { name: '调用详情' });
  expect(await within(dialog).findByText(/演示数据：离线固定样例/)).toBeVisible();
  expect(within(dialog).getByRole('heading', { name: '返回结果 · 3 条' })).toBeVisible();
  expect(dialog.querySelectorAll('article.call-result')).toHaveLength(3);
  expect(within(dialog).getByText(/尚不代表已确认匹配/)).toBeVisible();
  const detailPath = requests.find(path => /\/calls\/[^?]/.test(path));
  expect(detailPath).toBeTruthy();
  expect((await (await nativeFetch(`${base}${detailPath}`)).json()).mode).toBe('demo');
  await userEvent.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(buttons[0]).toHaveFocus();
  expect(await (await nativeFetch(`${base}/api/tasks/${task.id}/report`)).text()).toBe(report);
  expect(requests.every(path => path.startsWith('/api/'))).toBe(true);
}, 15000);

it('searches knowledge, pages summaries and opens version evidence through the real API', async () => {
  const raw = '@article{search,title={BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding},author={Devlin, Jacob and Chang, Ming-Wei and Lee, Kenton and Toutanova, Kristina},year={2018},eprint={1810.04805}}';
  const boundary = 'knowledge-ui';
  const body = `--${boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\ndemo\r\n--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="knowledge.bib"\r\n\r\n${raw}\r\n--${boundary}--\r\n`;
  const task = await (await nativeFetch(`${base}/api/tasks`, { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body })).json() as Task;
  await nativeFetch(`${base}/api/events?task_id=${task.id}`).then(response => response.text());
  const searches: string[] = [];
  vi.stubGlobal('fetch', (input: string, options?: RequestInit) => {
    if (input.startsWith('/api/articles?')) searches.push(input);
    return nativeFetch(new URL(input, base), options);
  });
  render(<MemoryRouter initialEntries={['/knowledge?q=no-match&page=2']}><App /></MemoryRouter>);
  expect(await screen.findByText('0 篇文章')).toBeVisible();
  const user = userEvent.setup();
  const input = screen.getByRole('textbox', { name: '搜索知识库' });
  await user.clear(input);
  fireEvent.compositionStart(input);
  for (const value of ['b', 'be', 'bei']) fireEvent.change(input, { target: { value } });
  await new Promise(done => setTimeout(done, 350));
  expect(searches).toHaveLength(1);
  expect(input).toHaveValue('bei');
  fireEvent.change(input, { target: { value: 'BERT' } });
  fireEvent.compositionEnd(input);
  const row = await screen.findByRole('button', { name: /^BERT: Pre-training/ });
  expect(within(row).getByText(/2 个版本 · \d+ 条来源/)).toBeVisible();
  expect(within(row).getByText('演示数据')).toBeVisible();
  const bertSearches = searches.filter(path => new URL(path, base).searchParams.get('q') === 'BERT');
  expect(bertSearches).toHaveLength(1);
  expect(new URL(bertSearches[0], base).searchParams.get('page')).toBe('1');
  expect(searches).toHaveLength(2);
  await user.click(row);
  expect(await screen.findByRole('heading', { level: 2, name: /^BERT: Pre-training/ })).toBeVisible();
  expect(screen.getByText('推荐引用版本')).toBeVisible();
  expect(screen.getByRole('heading', { name: /出版版本\s*2/ })).toBeVisible();
  await waitFor(() => expect(screen.getAllByRole('button', { name: '原始快照' }).length).toBeGreaterThan(0));
  await user.click(screen.getByRole('combobox', { name: '知识库数据模式' }));
  await user.click(await screen.findByRole('option', { name: '在线知识' }));
  expect(await screen.findByText('0 篇文章')).toBeVisible();
  await user.click(screen.getByRole('combobox', { name: '知识库数据模式' }));
  await user.click(await screen.findByRole('option', { name: '演示样例' }));
  expect(await screen.findByRole('button', { name: /^BERT: Pre-training/ })).toBeVisible();
}, 15000);

it('uses API categories for the leading status, filters and the five task metrics', async () => {
  const raw = '@misc{found,title={BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding},author={Devlin, Jacob and Chang, Ming-Wei and Lee, Kenton and Toutanova, Kristina},year={2018},eprint={1810.04805}}\n@article{missing,title={Synthetic unmatched reference},journal={Input Journal}}\n@patent{patent,title={Synthetic patent}}';
  const boundary = 'classification-ui';
  const body = `--${boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\ndemo\r\n--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="classes.bib"\r\n\r\n${raw}\r\n--${boundary}--\r\n`;
  const task = await (await nativeFetch(`${base}/api/tasks`, { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body })).json() as Task;
  const events = await (await nativeFetch(`${base}/api/events?task_id=${task.id}`)).text();
  expect(events).toContain('event: done');
  const current = await (await nativeFetch(`${base}/api/tasks/${task.id}`)).json() as Task;
  expect(current.summary).toMatchObject({ total: 3, completed: 3, not_found: 1, conflicts: 1, unprocessed: 0 });
  const entries = await (await nativeFetch(`${base}/api/tasks/${task.id}/entries`)).json() as Page<EntrySummary>;
  expect(entries.items.map(entry => entry.classification)).toEqual(['version_confirmed', 'not_found', 'unverifiable']);
  connect();
  render(<MemoryRouter initialEntries={[`/tasks/${task.id}`]}><App /></MemoryRouter>);
  const table = await screen.findByRole('table', { name: '本次文件引用核查结果' });
  expect(within(table).getAllByRole('columnheader').map(node => node.textContent)).toEqual(['状态', '引用', '第一作者', '年份', '操作']);
  expect(within(table).queryByRole('columnheader', { name: '主要建议' })).not.toBeInTheDocument();
  expect(within(table).getByText('存在字段冲突').closest('td')).toHaveClass('entry-status-warning');
  expect(within(table).getByText('演示数据 · 版本已确认')).toBeVisible();
  expect(within(table).getByText('不支持核验')).toBeVisible();
  expect(entries.items.find(entry => entry.citation_key === 'missing')?.journal).toBe('Input Journal');
  const citation = within(table).getByText('Input Journal').closest('td')!;
  expect(within(citation).getByText('missing')).toHaveClass('citation-key-pill');
  expect(Array.from(document.querySelectorAll('.task-metric-label')).map(node => node.textContent)).toEqual([
    '总条目', '已处理', '未找到匹配', '有字段冲突的', '未处理的',
  ]);
  expect(Array.from(document.querySelectorAll('.task-metric-value')).map(node => node.textContent)).toEqual(['3', '3100%', '1', '1', '0']);
  const user = userEvent.setup();
  expect(screen.queryByRole('combobox', { name: '类型筛选' })).not.toBeInTheDocument();
  const filters = within(screen.getByRole('toolbar', { name: '条目筛选' }));
  const types = within(filters.getByRole('group', { name: '类型筛选' }));
  expect(types.getAllByRole('button')).toHaveLength(4);
  expect(types.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true');
  expect(types.queryByRole('button', { name: '字段冲突' })).not.toBeInTheDocument();
  const unavailable = filters.getByRole('button', { name: '无法核验' });
  const confirmed = filters.getByRole('button', { name: '版本已确认' });
  const conflicts = filters.getByRole('button', { name: '字段冲突' });
  await user.click(unavailable);
  await waitFor(() => expect(within(screen.getByRole('table', { name: '本次文件引用核查结果' })).getAllByRole('row')).toHaveLength(2));
  expect(unavailable).toHaveAttribute('aria-pressed', 'true');
  expect(types.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'false');
  await user.click(unavailable);
  await waitFor(() => expect(within(screen.getByRole('table', { name: '本次文件引用核查结果' })).getAllByRole('row')).toHaveLength(4));
  expect(unavailable).toHaveAttribute('aria-pressed', 'false');
  expect(types.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true');
  await user.click(confirmed);
  fireEvent.change(filters.getByRole('textbox', { name: '搜索条目' }), { target: { value: 'unsubmitted draft' } });
  await user.click(filters.getByRole('button', { name: '重置' }));
  expect(filters.getByRole('textbox', { name: '搜索条目' })).toHaveValue('');
  await new Promise(done => setTimeout(done, 350));
  await waitFor(() => expect(within(screen.getByRole('table', { name: '本次文件引用核查结果' })).getAllByRole('row')).toHaveLength(4));
  expect(types.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true');
  await user.click(confirmed);
  await waitFor(() => expect(within(screen.getByRole('table', { name: '本次文件引用核查结果' })).getAllByRole('row')).toHaveLength(2));
  await user.click(conflicts);
  expect(confirmed).toHaveAttribute('aria-pressed', 'true');
  expect(conflicts).toHaveAttribute('aria-pressed', 'true');
  await user.click(unavailable);
  expect(await screen.findByRole('heading', { name: '没有匹配的条目' })).toBeVisible();
  expect(confirmed).toHaveAttribute('aria-pressed', 'false');
  expect(unavailable).toHaveAttribute('aria-pressed', 'true');
  expect(conflicts).toHaveAttribute('aria-pressed', 'true');
  await user.click(conflicts);
  await waitFor(() => expect(within(screen.getByRole('table', { name: '本次文件引用核查结果' })).getAllByRole('row')).toHaveLength(2));
  const filteredTable = screen.getByRole('table', { name: '本次文件引用核查结果' });
  expect(within(filteredTable).getByText('Synthetic patent')).toBeVisible();
  expect(within(filteredTable).queryByText('版本已确认')).not.toBeInTheDocument();
  await user.click(within(filteredTable).getByRole('link', { name: 'Synthetic patent' }));
  const back = await screen.findByRole('link', { name: '返回任务' });
  const target = new URL(back.getAttribute('href')!, base);
  expect(target.pathname).toBe(`/tasks/${task.id}`);
  expect(target.searchParams.get('classification')).toBe('unverifiable');
  expect(target.searchParams.get('page')).toBe('1');
  expect(target.searchParams.has('conflict')).toBe(false);
}, 20000);

it('filters upgrades through the API and preserves filters across detail pagination and reset', async () => {
  const raw = Array.from({ length: 11 }, (_, i) => `@misc{upgrade${i},title={BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding},author={Devlin, Jacob and Chang, Ming-Wei and Lee, Kenton and Toutanova, Kristina},year={2018},eprint={1810.04805}}`).join('\n') + '\n@misc{missing,title={Unmatched upgrade test}}';
  const boundary = 'upgrade-filter-ui';
  const body = `--${boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\ndemo\r\n--${boundary}\r\nContent-Disposition: form-data; name="confirm_budget"\r\n\r\ntrue\r\n--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="upgrades.bib"\r\n\r\n${raw}\r\n--${boundary}--\r\n`;
  const task = await (await nativeFetch(`${base}/api/tasks`, { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body })).json() as Task;
  await (await nativeFetch(`${base}/api/events?task_id=${task.id}`)).text();
  connect();
  render(<MemoryRouter initialEntries={[`/tasks/${task.id}?page=2`]}><App /></MemoryRouter>);
  const user = userEvent.setup();
  const upgrade = await screen.findByRole('button', { name: '找到新版本' });
  await user.click(upgrade);
  expect(upgrade).toHaveAttribute('aria-pressed', 'true');
  await screen.findByText('共 11 条');
  expect(screen.getByText('第 1 / 2 页')).toBeVisible();
  await user.click(screen.getByRole('button', { name: '版本已确认' }));
  await screen.findByText('共 11 条');
  await user.click(screen.getByRole('button', { name: '字段冲突' }));
  await user.click(await screen.findByRole('button', { name: '下一页' }));
  await screen.findByText('第 2 / 2 页');
  await waitFor(() => expect(screen.getAllByRole('link', { name: /^BERT: Pre-training/ })).toHaveLength(1));
  await user.click(screen.getByRole('link', { name: /^BERT: Pre-training/ }));
  await user.click(await screen.findByRole('button', { name: '上一条' }));
  // The route updates before the new entry request finishes; wait for its
  // content so the return link belongs to the current mounted detail page.
  await screen.findAllByText('upgrade9');
  const back = await screen.findByRole('link', { name: '返回任务' });
  await waitFor(() => expect(new URL(back.getAttribute('href')!, base).searchParams.get('page')).toBe('1'));
  const params = new URL(back.getAttribute('href')!, base).searchParams;
  expect(params.get('upgrade')).toBe('true');
  expect(params.get('classification')).toBe('version_confirmed');
  expect(params.get('conflict')).toBe('true');
  await user.click(back);
  fireEvent.change(await screen.findByRole('textbox', { name: '搜索条目' }), { target: { value: 'missing' } });
  expect(await screen.findByRole('heading', { name: '没有匹配的条目' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: '重置' }));
  await screen.findByText('共 12 条');
  expect(screen.getByRole('button', { name: '找到新版本' })).toHaveAttribute('aria-pressed', 'false');
  expect(screen.getByRole('textbox', { name: '搜索条目' })).toHaveValue('');
}, 30000);

it('updates a running task through actual SSE and stops all background reads after finalization', async () => {
  const raw = Array.from({ length: 12 }, (_, index) => `@misc{live${index},title={Offline SSE example ${index}}}`).join('\n');
  const boundary = 'offline-sse-ui';
  const body = `--${boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\ndemo\r\n--${boundary}\r\nContent-Disposition: form-data; name="confirm_budget"\r\n\r\ntrue\r\n--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="live.bib"\r\n\r\n${raw}\r\n--${boundary}--\r\n`;
  const uploaded = await nativeFetch(`${base}/api/tasks`, { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body });
  expect(uploaded.status).toBe(201);
  const task = await uploaded.json() as Task;
  ApiEvents.base = base;
  vi.stubGlobal('EventSource', ApiEvents);
  let reads = 0;
  vi.stubGlobal('fetch', (input: string | URL, options?: RequestInit) => {
    if (String(input).startsWith('/api/')) reads++;
    return nativeFetch(new URL(input, base), options);
  });
  render(<MemoryRouter initialEntries={[`/tasks/${task.id}`]}><App /></MemoryRouter>);
  await waitFor(() => expect(ApiEvents.instances.length).toBeGreaterThan(0), { timeout: 10000 });
  await waitFor(() => expect(ApiEvents.instances[0].kinds).toContain('change'), { timeout: 10000 });
  await waitFor(() => expect(ApiEvents.instances[0].kinds).toContain('done'), { timeout: 10000 });
  expect(ApiEvents.instances[0].closed).toBe(true);
  await waitFor(() => expect(screen.getByText('100%')).toBeVisible());
  const current = await (await nativeFetch(`${base}/api/tasks/${task.id}`)).json() as Task;
  expect(current.finalized).toBe(true);
  expect(current.summary.completed).toBe(12);
  await new Promise(done => setTimeout(done, 200));
  const settledReads = reads;
  await new Promise(done => setTimeout(done, 3300));
  expect(reads).toBe(settledReads);
  expect(ApiEvents.instances).toHaveLength(1);
}, 20000);

it.each(['list', 'entry'])('keeps the %s page subscribed until its parent task is frozen', async page => {
  const reference = 'title={BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding}, author={Devlin, Jacob and Chang, Ming-Wei and Lee, Kenton and Toutanova, Kristina}, year={2018}, eprint={1810.04805}';
  const raw = Array.from({ length: 12 }, (_, index) => `@article{observe${index},${reference}}`).join('\n');
  const boundary = `sse-${page}`;
  const body = `--${boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\ndemo\r\n--${boundary}\r\nContent-Disposition: form-data; name="confirm_budget"\r\n\r\ntrue\r\n--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${page}.bib"\r\n\r\n${raw}\r\n--${boundary}--\r\n`;
  const task = await (await nativeFetch(`${base}/api/tasks`, { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body })).json() as Task;
  const entries = await (await nativeFetch(`${base}/api/tasks/${task.id}/entries`)).json();
  ApiEvents.base = base;
  vi.stubGlobal('EventSource', ApiEvents);
  vi.stubGlobal('fetch', (input: string | URL, options?: RequestInit) => nativeFetch(new URL(input, base), options));
  // The list deliberately excludes running tasks; active_tasks must keep it live.
  const route = page === 'list' ? `/tasks?status=completed&q=${page}.bib` : `/tasks/${task.id}/entries/${entries.items[0].id}`;
  render(<MemoryRouter initialEntries={[route]}><App /></MemoryRouter>);
  await waitFor(() => expect(ApiEvents.instances.length).toBeGreaterThan(0), { timeout: 10000 });
  await waitFor(() => expect(ApiEvents.instances[0].kinds).toContain('done'), { timeout: 15000 });
  expect(ApiEvents.instances[0].closed).toBe(true);
  if (page === 'list') expect(await screen.findByRole('link', { name: 'list.bib' })).toBeVisible();
  else expect(await screen.findByText('Multiple entries cite the same confirmed publication version; none were removed')).toBeVisible();
  const frozen = await (await nativeFetch(`${base}/api/tasks/${task.id}/report`)).json();
  expect(frozen.task.finalized).toBe(true);
}, 25000);

it('renders actual task warnings, switches publication as a whole and exports through the API', async () => {
  const raw = '% preserve original\r\n@article{bert, title={BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding}, author={Devlin, Jacob and Chang, Ming-Wei and Lee, Kenton and Toutanova, Kristina}, year={2018}, eprint={1810.04805}, archiveprefix={arXiv}, custom={keep}}\r\n';
  const boundary = 'cite-audit-offline-ui';
  const body = `--${boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\ndemo\r\n--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="demo.bib"\r\nContent-Type: application/x-bibtex\r\n\r\n${raw}\r\n--${boundary}--\r\n`;
  const uploaded = await nativeFetch(`${base}/api/tasks`, { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body });
  expect(uploaded.status).toBe(201);
  const task = await uploaded.json() as Task;
  await waitFor(async () => {
    const current = await (await nativeFetch(`${base}/api/tasks/${task.id}`)).json() as Task;
    expect(current.execution_status).toBe('completed');
  }, { timeout: 10000 });
  let artifact: ExportArtifact | undefined;
  connect(result => { artifact = result; });
  render(<MemoryRouter initialEntries={[`/tasks/${task.id}`]}><App /></MemoryRouter>);
  expect(await screen.findByText('Demonstration fixtures; not online verification')).toBeVisible();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: '校订预览 / 导出' }));
  await user.click(await screen.findByRole('radio', { name: /整体改用推荐版本/ }));
  await user.click(screen.getByRole('button', { name: '预览 1 项选择' }));
  await user.click(await screen.findByRole('checkbox', { name: '我已审阅差异和未决项，确认生成新文件，保留原件。' }));
  await user.click(screen.getByRole('button', { name: '确认生成新文件' }));
  expect(await screen.findByRole('button', { name: '下载新 Bib' })).toBeVisible();
  expect(artifact).toBeDefined();
  const corrected = await (await nativeFetch(new URL(artifact!.download_url, base))).text();
  expect(corrected).toContain('2019');
  expect(corrected).toContain('custom={keep}');
  expect(corrected).not.toContain('eprint');
  expect(await (await nativeFetch(new URL(task.original_url, base))).text()).toBe(raw);
}, 20000);

it.each(['continue', 'adjust'])('handles the %s budget choice before creating a task through the real API', async choice => {
  const submissions: { config: string; confirmed: boolean; status: number }[] = [];
  vi.stubGlobal('fetch', async (input: string, options?: RequestInit) => {
    if (!(options?.body instanceof FormData)) return nativeFetch(new URL(input, base), options);
    // Bridge jsdom File/FormData to Node fetch without mocking the API or parser.
    const form = options.body;
    const boundary = 'budget-choice-ui';
    const parts: string[] = [];
    for (const [key, value] of form.entries()) {
      if (typeof value === 'string') parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="${key}"\r\n\r\n${value}\r\n`);
      else {
        const content = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader(); reader.onload = () => resolve(reader.result as string); reader.onerror = reject; reader.readAsText(value);
        });
        parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="${key}"; filename="${value.name}"\r\n\r\n${content}\r\n`);
      }
    }
    const result = await nativeFetch(new URL(input, base), { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body: parts.join('') + `--${boundary}--\r\n` });
    submissions.push({ config: form.get('config') as string, confirmed: form.get('confirm_budget') === 'true', status: result.status });
    return result;
  });
  const before = await (await nativeFetch(`${base}/api/tasks`)).json();
  const close = vi.fn();
  render(<MemoryRouter><UploadDialog onClose={close} /></MemoryRouter>);
  const user = userEvent.setup();
  const raw = '% @misc{comment,fake={ignored}}\n@string{heading="First"}\n@misc{a,title=heading}\n@misc{b,title={Second}}';
  await user.upload(screen.getByLabelText('完整 BibTeX 文件'), new File([raw], 'budget-ui.bib'));
  await user.click(screen.getByText('请求配置'));
  const total = await screen.findByRole('spinbutton', { name: '任务请求总上限' });
  await user.clear(total); await user.type(total, '20');
  await user.click(screen.getByRole('button', { name: '创建演示任务' }));
  expect(await screen.findByRole('heading', { name: '理论请求上限超过任务预算' })).toBeVisible();
  expect(screen.getByText('2 条 × 每条最多 18 次 = 36 次，超过任务请求总上限 20 次。')).toBeVisible();
  expect((await (await nativeFetch(`${base}/api/tasks`)).json()).total).toBe(before.total);
  expect(close).not.toHaveBeenCalled();
  if (choice === 'continue') await user.click(screen.getByRole('button', { name: '按当前预算继续' }));
  else {
    await user.click(screen.getByRole('button', { name: '调整预算' }));
    expect(screen.getByText('budget-ui.bib')).toBeVisible();
    const adjusted = screen.getByRole('spinbutton', { name: '任务请求总上限' });
    expect(adjusted).toHaveFocus();
    await user.clear(adjusted); await user.type(adjusted, '36');
    await user.click(screen.getByRole('button', { name: '创建演示任务' }));
  }
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(submissions.map(s => s.status)).toEqual([409, 201]);
  expect(submissions[0].confirmed).toBe(false);
  expect(submissions[1].confirmed).toBe(choice === 'continue');
  const tasks = await (await nativeFetch(`${base}/api/tasks`)).json() as Page<Task>;
  expect(tasks.total).toBe(before.total + 1);
  const task = tasks.items.find(item => item.filename === 'budget-ui.bib' && item.config.request_budget === (choice === 'continue' ? 20 : 36))!;
  expect(task.config.entry_request_budget).toBe(18);
  expect(await (await nativeFetch(new URL(task.original_url, base))).text()).toBe(raw);
}, 20000);

it('uses API limits and saves cache settings without resubmitting read-only fields', async () => {
  connect();
  render(<MemoryRouter initialEntries={['/settings/cache']}><App /></MemoryRouter>);
  const input = await screen.findByRole('spinbutton', { name: '成功结果有效期（秒）' }, { timeout: 10000 });
  expect(input).toHaveAttribute('max', '604800');
  const negative = screen.getByRole('spinbutton', { name: '未命中结果有效期（秒，当前固定）' });
  expect(negative).toHaveValue(60);
  expect(negative).toHaveAttribute('min', '60');
  expect(negative).toHaveAttribute('max', '60');
  const user = userEvent.setup();
  await user.clear(input);
  await user.type(input, '604800');
  await user.click(screen.getByRole('button', { name: '保存设置' }));
  expect(await screen.findByText('设置已保存')).toBeVisible();
  const settings = await (await nativeFetch(`${base}/api/settings`)).json() as Settings;
  expect(settings.cache.positive_ttl_seconds).toBe(604800);
  expect(settings.limits.runtime.max_upload_bytes.max).toBe(8388608);
  expect(settings.model.has_api_key).toBe(false);
  expect('api_key' in settings.model).toBe(false);
}, 20000);

it('separates semantic assessments and untouched auxiliary fields from a real API report', async () => {
  const raw = '@article{semantic, title={BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding}, author={Devlin, Jacob and Chang, Ming-Wei and Lee, Kenton and Toutanova, Kristina}, year={2018}, eprint={1810.04805}, archiveprefix={arXiv}, eprinttype={arXiv}, file={local-only.pdf}}';
  const boundary = 'semantic-fields-ui';
  const body = `--${boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\ndemo\r\n--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="semantic.bib"\r\nContent-Type: application/x-bibtex\r\n\r\n${raw}\r\n--${boundary}--\r\n`;
  const uploaded = await nativeFetch(`${base}/api/tasks`, { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body });
  expect(uploaded.status).toBe(201);
  const task = await uploaded.json() as Task;
  await waitFor(async () => {
    expect((await (await nativeFetch(`${base}/api/tasks/${task.id}`)).json()).execution_status).toBe('completed');
  }, { timeout: 10000 });
  const report = await (await nativeFetch(`${base}/api/tasks/${task.id}/report`)).json();
  const entry = report.entries[0];
  expect(entry.auxiliary_fields.map((item: { field: string }) => item.field)).toEqual(['file']);
  expect(entry.field_assessments.filter((item: { semantic_key: string }) => item.semantic_key === 'eprint_namespace')).toHaveLength(1);
  connect();
  ApiEvents.base = base;
  vi.stubGlobal('EventSource', ApiEvents);
  render(<MemoryRouter initialEntries={[`/tasks/${task.id}/entries/${entry.id}`]}><App /></MemoryRouter>);
  expect(await screen.findByText('未纳入核查的原始字段')).toBeVisible();
  const table = screen.getByRole('columnheader', { name: '上传 Bib 原值' }).closest('table')!;
  expect(within(table).getByText('仓储命名空间')).toBeVisible();
  expect(within(table).queryByText('file')).not.toBeInTheDocument();
  await userEvent.click(screen.getByText('查看 1 个字段 · 未经核实'));
  expect(screen.getByText('local-only.pdf')).toBeVisible();
  const preview = await (await nativeFetch(`${base}/api/tasks/${task.id}/export-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ selections: [] }),
  })).json();
  expect(preview.bibtex).toBe(raw);
  expect(preview.warnings.join(' ')).toContain('导出不代表所有字段已核验');
  await userEvent.click(screen.getByRole('button', { name: '重新核查此条目' }));
  expect(new URL(screen.getByRole('link', { name: '返回任务' }).getAttribute('href')!, base).pathname).toBe(`/tasks/${task.id}`);
  expect(screen.getByRole('columnheader', { name: '上传 Bib 原值' })).toBeVisible();
  await waitFor(async () => {
    const current = await (await nativeFetch(`${base}/api/tasks/${task.id}`)).json() as Task;
    expect(current.report_revision).toBe(2);
    expect(current.finalized).toBe(true);
    expect(screen.getByRole('button', { name: '重新核查此条目' })).toBeEnabled();
    // A fast retry may finalize before the refresh observes a live task, so
    // no stream is needed. Any stream actually opened must be closed.
    expect(ApiEvents.instances.every(stream => stream.closed)).toBe(true);
  }, { timeout: 10000 });
  expect(await (await nativeFetch(`${base}/api/tasks/${task.id}/reports/1`)).json()).toEqual(report);
  const latest = await (await nativeFetch(`${base}/api/tasks/${task.id}/report`)).json();
  expect(latest.entries[0].id).toBe(entry.id);
  expect(latest.task.report_revision).toBe(2);
  await userEvent.click(screen.getByRole('link', { name: '返回任务' }));
  await userEvent.click(await screen.findByRole('tab', { name: '原件信息' }));
  expect(await screen.findByRole('button', { name: '下载第 1 次核查报告' })).toBeVisible();
  expect(screen.getByRole('button', { name: '下载第 2 次核查报告' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '重新核查当前任务' }));
  await waitFor(async () => {
    const current = await (await nativeFetch(`${base}/api/tasks/${task.id}`)).json() as Task;
    expect(current.report_revision).toBe(3);
    expect(current.finalized).toBe(true);
    expect(screen.getByRole('button', { name: '重新核查当前任务' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '下载第 3 次核查报告' })).toBeVisible();
  }, { timeout: 10000 });
  const sameTask = await (await nativeFetch(`${base}/api/tasks?q=semantic.bib`)).json();
  expect(sameTask.total).toBe(1);
  expect(sameTask.items[0].id).toBe(task.id);
}, 20000);
