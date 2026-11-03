import { useCallback, useEffect, useRef, useState } from 'react';

const isAbort = (error: unknown) => typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError';

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: Record<string, unknown>;
  constructor(message: string, status: number, code?: string, details?: Record<string, unknown>) {
    super(message); this.status = status; this.code = code; this.details = details;
  }
}
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try { response = await fetch(path.startsWith('/api/') ? path : `/api${path}`, { ...options, headers: { ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...options.headers } }); }
  catch (error) { if (isAbort(error)) throw error; throw new ApiError('无法连接本地服务，请检查服务是否已启动后重试。', 0); }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    let code: string | undefined;
    let details: Record<string, unknown> | undefined;
    try {
      const body = await response.json(); const detail = body.detail ?? body.error ?? body.message;
      message = typeof detail === 'string' ? detail : detail?.message ?? (Array.isArray(detail) ? detail.map((d: { msg?: string }) => d.msg).join('；') : message);
      code = body.error?.code; details = body.error?.details;
    } catch { /* HTTP status remains useful without JSON. */ }
    throw new ApiError(message, response.status, code, details);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export function useResource<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pathRef = useRef(path);
  const reload = useRef<() => void>(() => {});
  const refresh = useCallback(() => reload.current(), []);
  useEffect(() => {
    if (!path) { setData(null); setLoading(false); return; }
    const changed = pathRef.current !== path; pathRef.current = path;
    if (changed) setData(null);
    let disposed = false; let running = false; let pending = false;
    const controller = new AbortController();
    const get = async () => {
      if (disposed) return;
      if (running) { pending = true; return; }
      running = true; pending = false;
      try { const result = await request<T>(path, { signal: controller.signal }); if (!disposed) { setData(result); setError(null); } }
      catch (e) { if (!disposed && !(isAbort(e))) setError(errorMessage(e)); }
      finally {
        running = false;
        if (!disposed) { setLoading(false); if (pending) void get(); }
      }
    };
    reload.current = () => { void get(); };
    const visible = () => { if (!document.hidden) void get(); };
    document.addEventListener('visibilitychange', visible);
    window.addEventListener('online', visible);
    setLoading(true); void get();
    return () => {
      disposed = true; reload.current = () => {}; controller.abort();
      document.removeEventListener('visibilitychange', visible);
      window.removeEventListener('online', visible);
    };
  }, [path]);
  return { data, loading, error, refresh, setData };
}
export const errorMessage = (e: unknown) => e instanceof Error ? e.message : '发生未知错误，请重试。';
export const json = (value: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(value) });
export async function download(path: string, fallback: string) {
  const response = await fetch(path.startsWith('/api/') ? path : `/api${path}`);
  if (!response.ok) throw new ApiError(`下载失败（${response.status}）`, response.status);
  const blob = await response.blob(); const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = fallback; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
