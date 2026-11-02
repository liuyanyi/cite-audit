import { useEffect, useRef } from 'react';
import type { Task } from './types';

export interface TaskChange { task_id: string; changed: string[]; entry_ids: string[] }
export const overviewChanged = (changes: TaskChange[] | null) =>
  changes === null || changes.some(change => change.changed.some(kind => kind !== 'call'));
export const isLiveTask = (task: Task) => ['queued', 'running'].includes(task.execution_status) || task.finalized === false;

// One stream per task page (all tasks for the list). REST remains the source of
// filtered pages and evidence; events only invalidate the resources on screen.
export function useTaskEvents(enabled: boolean, taskId: string | undefined, onChange: (changes: TaskChange[] | null) => void) {
  const callback = useRef(onChange);
  callback.current = onChange;
  useEffect(() => {
    if (!enabled || typeof EventSource === 'undefined') return;
    let source: EventSource | null = null;
    let stopped = false;
    let cursor = '';
    let timer: ReturnType<typeof setTimeout> | undefined;
    let recovery: ReturnType<typeof setTimeout> | undefined;
    let pending: TaskChange[] | null = [];
    const flush = () => {
      clearTimeout(timer); timer = undefined;
      const changes = pending; pending = [];
      callback.current(changes);
    };
    const schedule = (changes: TaskChange[] | null) => {
      pending = changes === null || pending === null ? null : [...pending, ...changes];
      // Keep one trailing refresh without repeatedly aborting slow HTTP reads.
      timer ??= setTimeout(flush, 100);
    };
    const open = () => {
      if (stopped || document.hidden || source) return;
      const params = new URLSearchParams();
      if (taskId) params.set('task_id', taskId);
      if (cursor) params.set('after', cursor);
      const connection = new EventSource(`/api/events?${params}`);
      source = connection;
      const receive = (handler: (event: MessageEvent) => void) => (event: Event) => {
        if (source !== connection) return;
        const message = event as MessageEvent;
        if (message.lastEventId) cursor = message.lastEventId;
        handler(message);
      };
      connection.addEventListener('sync', receive(() => schedule(null)));
      connection.addEventListener('change', receive(event => {
        try { schedule((JSON.parse(event.data) as { items: TaskChange[] }).items); }
        catch { schedule(null); }
      }));
      connection.addEventListener('done', receive(() => {
        stopped = true; connection.close(); source = null;
        clearTimeout(recovery); recovery = undefined;
        // Includes the last entry, duplicate warnings and the frozen report.
        pending = null; flush();
      }));
      connection.onopen = () => { if (source === connection) { clearTimeout(recovery); recovery = undefined; } };
      connection.onerror = () => {
        if (source !== connection || stopped) return;
        // EventSource reconnects with Last-Event-ID. Only a sustained failure
        // triggers a safety read; there is no concurrent normal polling loop.
        recovery ??= setTimeout(() => { recovery = undefined; schedule(null); }, 10000);
      };
    };
    const visibility = () => {
      if (document.hidden) {
        source?.close(); source = null;
        clearTimeout(timer); timer = undefined; pending = [];
        clearTimeout(recovery); recovery = undefined;
      } else open();
    };
    open();
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('online', visibility);
    return () => {
      stopped = true; source?.close(); clearTimeout(timer); clearTimeout(recovery);
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('online', visibility);
    };
  }, [enabled, taskId]);
}
