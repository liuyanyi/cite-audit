import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import Knowledge from '../pages/Knowledge';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function Navigation() {
  const location = useLocation(); const navigate = useNavigate();
  return <><output aria-label="当前路由">{location.search}</output><button onClick={() => navigate('/knowledge?q=external')}>外部导航</button></>;
}

it('coalesces typing into one search, resets paging and follows URL navigation', async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn<(url: string, options?: RequestInit) => Promise<Response>>(async () =>
    new Response(JSON.stringify({ items: [], total: 0, page: 2, page_size: 10 })));
  vi.stubGlobal('fetch', fetchMock);
  await act(async () => { render(<MemoryRouter initialEntries={['/knowledge?q=old&page=2&mode=demo']}><Knowledge /><Navigation /></MemoryRouter>); });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const input = screen.getByRole('textbox', { name: '搜索知识库' });
  for (const value of ['B', 'BE', 'BER', 'BERT']) fireEvent.change(input, { target: { value } });
  expect(input).toHaveValue('BERT');
  await act(async () => { await vi.advanceTimersByTimeAsync(249); });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  const request = new URL(fetchMock.mock.calls[1][0] as string, 'http://localhost');
  expect(request.searchParams.get('q')).toBe('BERT');
  expect(request.searchParams.get('page')).toBe('1');
  expect(request.searchParams.get('mode')).toBe('demo');
  expect(screen.getByLabelText('当前路由').textContent).not.toContain('page=');
  expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
  // A pending draft must not overwrite a later URL change.
  fireEvent.change(input, { target: { value: 'pending' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '外部导航' })); });
  expect(input).toHaveValue('external');
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(screen.getByLabelText('当前路由')).toHaveTextContent('?q=external');
});
