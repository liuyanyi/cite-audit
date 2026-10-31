import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import App from '../App';
import type { Settings } from '../types';

const fixture: Settings = {
  sources: [{ id: 'semantic_scholar', name: 'Semantic Scholar', enabled: true, implemented: true, supports_api_key: true, requires_key: false, has_api_key: true, status: 'not_tested', capabilities: ['lookup', 'search'] }],
  model: { enabled: true, library: 'mmsp', protocol: 'openai-chat', model: 'sample-model', base_url: 'https://api.example.org', has_api_key: true, timeout_seconds: 30, max_retries: 1, max_output_tokens: 1024, price_source: 'mmsp_registry', price: null },
  runtime: { request_budget: 100, timeout_seconds: 12, max_retries: 1, concurrency: 1, max_upload_bytes: 5000000 },
  cache: { positive_ttl_seconds: 3600, negative_ttl_seconds: 300, discovery_ttl_seconds: 86400 },
  audit: { enabled: true, retain_prompt_text: false, redact_secrets: true },
};
const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
    if (url === '/api/settings') return reply(fixture);
    if (url.startsWith('/api/calls')) return reply({ items: [], total: 0, page: 1, page_size: 20 });
    if (opts?.method === 'DELETE') return reply({ cleared: true, knowledge_preserved: true, history_preserved: true });
    return reply({ status: 'available' });
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());
const mount = async () => { render(<MemoryRouter initialEntries={['/settings/sources']}><App /></MemoryRouter>); await screen.findByRole('switch', { name: '启用 Semantic Scholar' }); return userEvent.setup(); };
const writes = () => fetchMock.mock.calls.filter(call => call[1]?.method === 'PUT');
const posts = () => fetchMock.mock.calls.filter(call => call[1]?.method === 'POST');
const deletes = () => fetchMock.mock.calls.filter(call => call[1]?.method === 'DELETE');

describe('settings configuration and confirmations', () => {
  it('renders one compact row per source with a trailing configuration dialog action', async () => {
    fetchMock.mockImplementation(async () => reply({ ...fixture, sources: [fixture.sources[0], { ...fixture.sources[0], id: 'crossref', name: 'Crossref', supports_api_key: false, has_api_key: false }] }));
    const user = await mount();
    const table = screen.getByRole('table', { name: '数据源配置' });
    expect(within(table).getAllByRole('row')).toHaveLength(3);
    expect(within(table).getAllByRole('columnheader').map(header => header.textContent)).toEqual(['数据源', '启用', '连接状态', '密钥', '操作']);
    for (const name of ['Semantic Scholar', 'Crossref']) {
      const row = within(table).getByRole('row', { name });
      expect(within(row).getByRole('rowheader')).toHaveTextContent(name);
      const lastCell = within(row).getAllByRole('cell').at(-1)!;
      const actions = within(lastCell).getAllByRole('button');
      expect(actions.at(-1)).toHaveAccessibleName(`配置 ${name}`);
      await user.click(actions.at(-1)!);
      expect(screen.getByRole('dialog')).toHaveAccessibleName(`配置 ${name}`);
      await user.keyboard('{Escape}');
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      await waitFor(() => expect(actions.at(-1)).toHaveFocus());
    }
    expect(writes()).toHaveLength(0);
    expect(posts()).toHaveLength(0);
  });
  it('omits unchanged keys and read-only presence fields when saving', async () => {
    const user = await mount();
    await user.click(screen.getByRole('switch', { name: '启用 Semantic Scholar' }));
    expect(writes()).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '保存设置' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    const payload = JSON.parse(writes()[0][1].body as string);
    expect(payload.sources[0]).toEqual({ id: 'semantic_scholar', enabled: false });
    expect(payload.model).not.toHaveProperty('api_key');
    expect(payload.model).not.toHaveProperty('has_api_key');
    expect(payload.model).not.toHaveProperty('price');
  });
  it('sends a typed source key only after save, without returning it into the field', async () => {
    const user = await mount();
    await user.click(screen.getByRole('button', { name: '配置 Semantic Scholar' }));
    await user.type(screen.getByLabelText('更换 Semantic Scholar 密钥'), 'test-secret-value');
    expect(writes()).toHaveLength(0);
    expect(screen.getByLabelText('更换 Semantic Scholar 密钥')).toHaveAttribute('type', 'password');
    await user.click(screen.getByRole('button', { name: '应用更改' }));
    expect(writes()).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '保存设置' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(JSON.parse(writes()[0][1].body as string).sources[0].api_key).toBe('test-secret-value');
    await waitFor(() => expect(screen.getByRole('button', { name: '保存设置' })).toBeDisabled());
    await user.click(screen.getByRole('button', { name: '配置 Semantic Scholar' }));
    expect(screen.getByLabelText('更换 Semantic Scholar 密钥')).toHaveValue('');
  });
  it('requires explicit billing confirmation for a model test and sends confirmed true', async () => {
    const user = await mount();
    await user.click(screen.getByRole('link', { name: '模型与密钥' }));
    await user.click(screen.getByRole('button', { name: '测试模型连接（可能计费）' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('可能产生费用');
    expect(posts()).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认发起请求' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0][0]).toBe('/api/settings/model/test');
    expect(JSON.parse(posts()[0][1].body as string)).toEqual({ confirmed: true });
  });
  it('does not call a source until the source test is explicitly confirmed', async () => {
    const user = await mount();
    await user.click(screen.getByRole('button', { name: '测试连接' }));
    expect(posts()).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '取消' }));
    expect(posts()).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '测试连接' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认发起请求' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0][0]).toBe('/api/settings/sources/semantic_scholar/test');
  });
  it('marks unsupported configurable values read-only and retains audit protection', async () => {
    const user = await mount();
    await user.click(screen.getByRole('link', { name: '预算与运行' }));
    expect(screen.getByLabelText('并发数（当前固定）')).toBeDisabled();
    expect(screen.getByLabelText('并发数（当前固定）')).toHaveValue(1);
    await user.click(screen.getByRole('link', { name: '缓存与复用' }));
    expect(screen.getByLabelText('未命中结果有效期（秒，当前固定）')).toBeDisabled();
    await user.click(screen.getByRole('link', { name: '调用审计' }));
    expect(screen.getByRole('checkbox', { name: '保存调用审计（始终开启）' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: '保存调用审计（始终开启）' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '保存提示文本（当前不保留）' })).not.toBeChecked();
  });
  it('cancels cache deletion safely and explains that knowledge and history are retained', async () => {
    const user = await mount();
    await user.click(screen.getByRole('link', { name: '缓存与复用' }));
    await user.click(screen.getByRole('button', { name: '清除查询缓存' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('历史任务报告不删除');
    expect(deletes()).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(deletes()).toHaveLength(0);
  });
  it('does not offer unused keys for Crossref', async () => {
    fetchMock.mockImplementation(async () => reply({ ...fixture, sources: [{ ...fixture.sources[0], id: 'crossref', name: 'Crossref', supports_api_key: false }] }));
    render(<MemoryRouter initialEntries={['/settings/sources']}><App /></MemoryRouter>);
    await screen.findByRole('switch', { name: '启用 Crossref' });
    await userEvent.click(screen.getByRole('button', { name: '配置 Crossref' }));
    expect(screen.getByText('无需 API key')).toBeVisible();
    expect(screen.queryByLabelText('更换 Crossref 密钥')).not.toBeInTheDocument();
  });
  it('discards source dialog changes on cancel without mutating the saved draft', async () => {
    const user = await mount();
    await user.click(screen.getByRole('button', { name: '配置 Semantic Scholar' }));
    await user.type(screen.getByLabelText('更换 Semantic Scholar 密钥'), 'discarded-secret');
    await user.click(within(screen.getByRole('dialog')).getByRole('switch', { name: '启用 Semantic Scholar' }));
    await user.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('switch', { name: '启用 Semantic Scholar' })).toBeChecked();
    expect(screen.getByRole('button', { name: '保存设置' })).toBeDisabled();
    expect(writes()).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '配置 Semantic Scholar' }));
    expect(screen.getByLabelText('更换 Semantic Scholar 密钥')).toHaveValue('');
  });
  it('filters source rows by source name without changing configuration', async () => {
    const user = await mount();
    await user.type(screen.getByRole('textbox', { name: '搜索数据源' }), 'Crossref');
    expect(screen.queryByRole('row', { name: 'Semantic Scholar' })).not.toBeInTheDocument();
    expect(screen.getByText('没有匹配的数据源')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '清除搜索' }));
    expect(screen.getByRole('row', { name: 'Semantic Scholar' })).toBeVisible();
    expect(writes()).toHaveLength(0);
  });
  it('filters rows with the enabled-state Radix select', async () => {
    const user = await mount();
    await user.click(screen.getByRole('combobox', { name: '数据源启用筛选' }));
    await user.click(screen.getByRole('option', { name: /^未启用$/ }));
    expect(screen.getByText('没有匹配的数据源')).toBeVisible();
    await user.click(screen.getByRole('combobox', { name: '数据源启用筛选' }));
    await user.click(screen.getByRole('option', { name: /^已启用$/ }));
    expect(screen.getByRole('row', { name: 'Semantic Scholar' })).toBeVisible();
    expect(screen.getByRole('button', { name: '保存设置' })).toBeDisabled();
  });
  it('shows unavailable integrations without enabling fake actions', async () => {
    fetchMock.mockImplementation(async () => reply({ ...fixture, sources: [{ ...fixture.sources[0], enabled: false, implemented: false, has_api_key: false, status: 'not_implemented' }] }));
    await mount();
    const card = within(screen.getByRole('row', { name: 'Semantic Scholar' }));
    expect(card.getByText('尚未接入')).toBeVisible();
    expect(card.getByRole('switch', { name: '启用 Semantic Scholar' })).toBeDisabled();
    expect(card.getByRole('switch', { name: '启用 Semantic Scholar' })).not.toBeChecked();
    expect(card.getByRole('button', { name: '测试连接' })).toBeDisabled();
    expect(card.getByRole('button', { name: '配置 Semantic Scholar' })).toBeDisabled();
    expect(posts()).toHaveLength(0);
  });
  it('blocks connection tests while unsaved configuration is pending', async () => {
    const user = await mount();
    await user.click(screen.getByRole('switch', { name: '启用 Semantic Scholar' }));
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled();
    await user.click(screen.getByRole('link', { name: '模型与密钥' }));
    expect(screen.getByRole('button', { name: '测试模型连接（可能计费）' })).toBeDisabled();
    expect(posts()).toHaveLength(0);
  });
});

function HistoryControls() {
  const navigate = useNavigate(); const location = useLocation();
  return <><button onClick={() => navigate(-1)}>测试后退</button><button onClick={() => navigate(1)}>测试前进</button><output aria-label="当前地址">{location.pathname}</output></>;
}
describe('settings primary sidebar tree', () => {
  it('opens a deep link with the matching primary-sidebar child active and no inner sidebar', async () => {
    render(<MemoryRouter initialEntries={['/settings/cache']}><App /></MemoryRouter>);
    expect(await screen.findByRole('heading', { level: 1, name: '缓存与复用' })).toBeVisible();
    const main = screen.getByRole('navigation', { name: '主导航' });
    expect(within(main).getByRole('button', { name: '设置' })).toHaveAttribute('aria-expanded', 'true');
    expect(within(main).getByRole('link', { name: '缓存与复用' })).toHaveAttribute('aria-current', 'page');
    expect(within(main).getByRole('link', { name: '数据源' })).not.toHaveAttribute('aria-current');
    expect(screen.queryByRole('navigation', { name: '设置分组' })).not.toBeInTheDocument();
    expect(document.querySelector('.settings-nav')).toBeNull();
  });
  it('supports collapse, expand and browser history between settings subpages', async () => {
    render(<MemoryRouter initialEntries={['/settings/sources']}><App /><HistoryControls /></MemoryRouter>);
    await screen.findByRole('switch', { name: '启用 Semantic Scholar' });
    await userEvent.click(screen.getByRole('button', { name: '设置' }));
    expect(screen.queryByRole('link', { name: '模型与密钥' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '设置' }));
    await userEvent.click(screen.getByRole('link', { name: '模型与密钥' }));
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/settings/model');
    await userEvent.click(screen.getByRole('link', { name: '预算与运行' }));
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/settings/runtime');
    await userEvent.click(screen.getByRole('button', { name: '测试后退' }));
    expect(screen.getByRole('heading', { level: 1, name: '模型与密钥' })).toBeVisible();
    expect(screen.getByRole('link', { name: '模型与密钥' })).toHaveAttribute('aria-current', 'page');
    await userEvent.click(screen.getByRole('button', { name: '测试前进' }));
    expect(screen.getByRole('heading', { level: 1, name: '预算与运行' })).toBeVisible();
  });
  it('redirects the old settings entry to sources and handles unknown sections', async () => {
    const view = render(<MemoryRouter initialEntries={['/settings']}><App /><HistoryControls /></MemoryRouter>);
    await screen.findByRole('switch', { name: '启用 Semantic Scholar' });
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/settings/sources'); view.unmount();
    render(<MemoryRouter initialEntries={['/settings/unknown']}><App /><HistoryControls /></MemoryRouter>);
    await screen.findByRole('switch', { name: '启用 Semantic Scholar' });
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('/settings/sources');
  });
  it('keeps unsaved values while switching settings sections', async () => {
    const user = await mount(); await user.click(screen.getByRole('switch', { name: '启用 Semantic Scholar' }));
    await user.click(screen.getByRole('link', { name: '模型与密钥' }));
    await user.click(screen.getByRole('link', { name: '数据源' }));
    expect(screen.getByRole('switch', { name: '启用 Semantic Scholar' })).not.toBeChecked();
    expect(screen.getByRole('button', { name: '保存设置' })).toBeEnabled(); expect(writes()).toHaveLength(0);
  });
  it('closes the mobile drawer after choosing a child and on Escape', async () => {
    await mount(); await userEvent.click(screen.getByRole('button', { name: '打开导航' }));
    expect(document.getElementById('main-sidebar')).toHaveClass('mobile-open');
    await userEvent.click(screen.getByRole('link', { name: '调用审计' }));
    expect(document.getElementById('main-sidebar')).not.toHaveClass('mobile-open');
    expect(screen.getByRole('link', { name: '调用审计' })).toHaveAttribute('aria-current', 'page');
    await userEvent.click(screen.getByRole('button', { name: '打开导航' })); await userEvent.keyboard('{Escape}');
    expect(document.getElementById('main-sidebar')).not.toHaveClass('mobile-open');
  });
});
