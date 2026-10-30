import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SettingsPage from '../pages/Settings';
import { ToastProvider } from '../components/ui';
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
const mount = async () => { render(<ToastProvider><SettingsPage /></ToastProvider>); await screen.findByRole('checkbox', { name: '启用 Semantic Scholar' }); return userEvent.setup(); };
const writes = () => fetchMock.mock.calls.filter(call => call[1]?.method === 'PUT');
const posts = () => fetchMock.mock.calls.filter(call => call[1]?.method === 'POST');
const deletes = () => fetchMock.mock.calls.filter(call => call[1]?.method === 'DELETE');

describe('settings configuration and confirmations', () => {
  it('omits unchanged keys and read-only presence fields when saving', async () => {
    const user = await mount();
    await user.click(screen.getByRole('checkbox', { name: '启用 Semantic Scholar' }));
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
    await user.click(screen.getByText('API key · 已保存'));
    await user.type(screen.getByLabelText('更换 Semantic Scholar 密钥'), 'test-secret-value');
    expect(writes()).toHaveLength(0);
    expect(screen.getByLabelText('更换 Semantic Scholar 密钥')).toHaveAttribute('type', 'password');
    await user.click(screen.getByRole('button', { name: '保存设置' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(JSON.parse(writes()[0][1].body as string).sources[0].api_key).toBe('test-secret-value');
    await waitFor(() => expect(screen.getByLabelText('更换 Semantic Scholar 密钥')).toHaveValue(''));
  });
  it('requires explicit billing confirmation for a model test and sends confirmed true', async () => {
    const user = await mount();
    await user.click(screen.getByRole('button', { name: '模型与密钥' }));
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
    await user.click(screen.getByRole('button', { name: '预算与运行' }));
    expect(screen.getByLabelText('并发数（当前固定）')).toBeDisabled();
    expect(screen.getByLabelText('并发数（当前固定）')).toHaveValue(1);
    await user.click(screen.getByRole('button', { name: '缓存与复用' }));
    expect(screen.getByLabelText('未命中结果有效期（秒，当前固定）')).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '调用审计' }));
    expect(screen.getByRole('checkbox', { name: '保存调用审计（始终开启）' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: '保存调用审计（始终开启）' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '保存提示文本（当前不保留）' })).not.toBeChecked();
  });
  it('cancels cache deletion safely and explains that knowledge and history are retained', async () => {
    const user = await mount();
    await user.click(screen.getByRole('button', { name: '缓存与复用' }));
    await user.click(screen.getByRole('button', { name: '清除查询缓存' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('历史任务报告不删除');
    expect(deletes()).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(deletes()).toHaveLength(0);
  });
  it('does not offer unused keys for Crossref', async () => {
    fetchMock.mockImplementation(async () => reply({ ...fixture, sources: [{ ...fixture.sources[0], id: 'crossref', name: 'Crossref', supports_api_key: false }] }));
    render(<ToastProvider><SettingsPage /></ToastProvider>);
    await screen.findByRole('checkbox', { name: '启用 Crossref' });
    expect(screen.queryByText('API key · 已保存')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('更换 Crossref 密钥')).not.toBeInTheDocument();
  });
  it('blocks connection tests while unsaved configuration is pending', async () => {
    const user = await mount();
    await user.click(screen.getByRole('checkbox', { name: '启用 Semantic Scholar' }));
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '模型与密钥' }));
    expect(screen.getByRole('button', { name: '测试模型连接（可能计费）' })).toBeDisabled();
    expect(posts()).toHaveLength(0);
  });
});
