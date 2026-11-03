import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useResource } from '../api';
import { overviewChanged, useTaskEvents } from '../task-events';

class FakeEvents extends EventTarget {
  static instances: FakeEvents[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { super(); FakeEvents.instances.push(this); }
  close() { this.closed = true; }
  emit(kind: string, data: unknown, id = '10') {
    this.dispatchEvent(new MessageEvent(kind, { data: JSON.stringify(data), lastEventId: id }));
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeEvents.instances = [];
  vi.stubGlobal('EventSource', FakeEvents);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('refreshes overviews on entry, task and sync events but skips call-only batches', () => {
  const change = (changed: string[]) => ({ task_id: 'one', entry_ids: [], changed });
  expect(overviewChanged([change(['call']), change(['call'])])).toBe(false);
  for (const kind of ['entry', 'task', 'deleted']) {
    expect(overviewChanged([change(['call']), change([kind])])).toBe(true);
  }
  expect(overviewChanged(null)).toBe(true);
});

it('coalesces progress, syncs once on done and never polls after terminal completion', async () => {
  const change = vi.fn();
  const { unmount } = renderHook(() => useTaskEvents(true, 'task_one', change));
  const stream = FakeEvents.instances[0];
  expect(stream.url).toContain('task_id=task_one');
  act(() => {
    stream.emit('sync', { reset: true, task_ids: ['task_one'] });
    for (let i = 0; i < 20; i++) stream.emit('change', { items: [{ task_id: 'task_one', changed: ['entry'], entry_ids: ['e1'] }] });
  });
  await act(() => vi.advanceTimersByTimeAsync(100));
  expect(change).toHaveBeenCalledTimes(1);
  act(() => stream.emit('done', { task_id: 'task_one' }));
  expect(change).toHaveBeenCalledTimes(2);
  expect(stream.closed).toBe(true);
  await act(() => vi.advanceTimersByTimeAsync(60000));
  expect(change).toHaveBeenCalledTimes(2);
  expect(FakeEvents.instances).toHaveLength(1);
  unmount();
});

it('does not open any stream for a finalized task', () => {
  renderHook(() => useTaskEvents(false, 'done', vi.fn()));
  expect(FakeEvents.instances).toHaveLength(0);
});

it('opens a fresh stream on retry and closes it when the REST refresh observes completion', () => {
  const { rerender, unmount } = renderHook(({ live }) => useTaskEvents(live, 'same-task', vi.fn()),
    { initialProps: { live: false } });
  expect(FakeEvents.instances).toHaveLength(0);
  rerender({ live: true });
  const first = FakeEvents.instances[0];
  expect(first.closed).toBe(false);
  rerender({ live: false });
  expect(first.closed).toBe(true);
  rerender({ live: true });
  expect(FakeEvents.instances).toHaveLength(2);
  const second = FakeEvents.instances[1];
  expect(second.closed).toBe(false);
  act(() => second.emit('done', { task_id: 'same-task' }));
  expect(second.closed).toBe(true);
  unmount();
});

it('closes when hidden, resumes from the cursor, and cleans up on navigation', () => {
  let hidden = false;
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
  const { rerender, unmount } = renderHook(({ id }) => useTaskEvents(true, id, vi.fn()), { initialProps: { id: 'first' } });
  const first = FakeEvents.instances[0];
  act(() => first.emit('sync', { reset: true, task_ids: ['first'] }, '123'));
  act(() => { hidden = true; document.dispatchEvent(new Event('visibilitychange')); });
  expect(first.closed).toBe(true);
  act(() => { hidden = false; document.dispatchEvent(new Event('visibilitychange')); });
  expect(FakeEvents.instances[1].url).toContain('after=123');
  rerender({ id: 'second' });
  expect(FakeEvents.instances[1].closed).toBe(true);
  expect(FakeEvents.instances[2].url).not.toContain('after=123');
  unmount();
  expect(FakeEvents.instances[2].closed).toBe(true);
});

it('uses a delayed safety read only during SSE failure and cancels it on recovery', async () => {
  const change = vi.fn();
  renderHook(() => useTaskEvents(true, 'one', change));
  const stream = FakeEvents.instances[0];
  act(() => stream.onerror?.());
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(change).not.toHaveBeenCalled();
  act(() => stream.onopen?.());
  await act(() => vi.advanceTimersByTimeAsync(10000));
  expect(change).not.toHaveBeenCalled();
  act(() => stream.onerror?.());
  await act(() => vi.advanceTimersByTimeAsync(10100));
  expect(change).toHaveBeenCalledTimes(1);
});

it('does not abort a slow HTTP read when further progress events arrive', async () => {
  let resolve: (response: Response) => void = () => {};
  const fetch = vi.fn<(path: string, options?: RequestInit) => Promise<Response>>()
    .mockImplementationOnce(() => new Promise(done => { resolve = done; }))
    .mockResolvedValue(new Response(JSON.stringify({ value: 'latest' })));
  vi.stubGlobal('fetch', fetch);
  const { result } = renderHook(() => useResource<{ value: string }>('/slow'));
  act(() => { result.current.refresh(); result.current.refresh(); });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][1]?.signal?.aborted).toBe(false);
  await act(async () => { resolve(new Response(JSON.stringify({ value: 'first' }))); });
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(result.current.data?.value).toBe('latest');
});
