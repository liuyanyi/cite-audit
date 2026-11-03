import { afterEach, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CallTable from '../components/CallTable';
import type { CallRecord } from '../types';

const call: CallRecord = { id: 'c1', task_id: null, entry_id: null, provider: 'crossref', kind: 'source', operation: 'search', status: 'success', started_at: '2026-11-02T00:00:00Z', duration_ms: 80, attempt: 1, cache_hit: false, mode: 'online', error_code: null, message: null, usage: null, cost: { known: false, amount: null, currency: null, price_snapshot: null } };
afterEach(() => vi.unstubAllGlobals());

it('loads only the selected call and shows its query, plain-text response and unknown cost', async () => {
  const fetch = vi.fn(async (_input: string) => new Response(JSON.stringify({ ...call,
    request_summary: 'https://api.crossref.org/works?query.title=Test+Paper&rows=3', http_status: 200,
    response_summary: { body: '<script>alert(1)</script>', byte_size: 25, http_status: 200, truncated: true },
  }), { status: 200 }));
  vi.stubGlobal('fetch', fetch);
  render(<CallTable calls={[call, { ...call, id: 'c2', provider: 'arxiv' }]} />);
  expect(fetch).not.toHaveBeenCalled();
  const user = userEvent.setup();
  const trigger = screen.getByRole('button', { name: '查看 Crossref search 调用详情' });
  await user.click(trigger);
  const dialog = await screen.findByRole('dialog', { name: '调用详情' });
  expect(await within(dialog).findByText('Test Paper')).toBeVisible();
  expect(within(dialog).getByText('query.title')).toBeVisible();
  expect(within(dialog).getByLabelText('来源响应内容')).toHaveTextContent('<script>alert(1)</script>');
  expect(dialog.querySelector('script')).toBeNull();
  expect(within(dialog).getByText(/仅保存前 32,000/)).toBeVisible();
  expect(within(dialog).getByText('未知')).toBeVisible();
  expect(fetch.mock.calls[0][0]).toBe('/api/calls/c1');
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(trigger).toHaveFocus();
});

it('keeps missing historical response details explicit and retries failed reads', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response('{"message":"暂时无法读取"}', { status: 503 }))
    .mockResolvedValue(new Response(JSON.stringify({ ...call, status: 'failed', message: 'TimeoutError' }), { status: 200 }));
  vi.stubGlobal('fetch', fetch);
  const user = userEvent.setup();
  render(<CallTable calls={[call]} />);
  await user.click(screen.getByRole('button', { name: /调用详情/ }));
  expect(await screen.findByRole('alert')).toHaveTextContent('暂时无法读取');
  await user.click(screen.getByRole('button', { name: '重试' }));
  const dialog = screen.getByRole('dialog');
  expect(await within(dialog).findByText('TimeoutError')).toBeVisible();
  expect(within(dialog).getByText('此记录未保存请求内容。')).toBeVisible();
  expect(within(dialog).getByText(/无法还原当时的搜索结果/)).toBeVisible();
  await user.click(within(dialog).getByRole('button', { name: '关闭' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
