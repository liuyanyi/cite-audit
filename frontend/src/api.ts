import { issueText } from "./ui";

type ApiOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  timeout?: number;
};

/** Writes are never retried: a timeout may have occurred after the server committed. */
export async function api<T = Record<string, unknown>>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  const { body, timeout = 90_000, ...request } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = new Headers(request.headers);
  const isForm = body instanceof FormData;
  if (body !== undefined && !isForm)
    headers.set("Content-Type", "application/json");
  try {
    const response = await fetch(path, {
      ...request,
      headers,
      body:
        body === undefined ? undefined : isForm ? body : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
    const text = await response.text();
    let data: Record<string, unknown>;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { message: text.slice(0, 300) };
    }
    if (!response.ok) {
      const detail = data.detail || data.error || data.message;
      throw new Error(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map(issueText).join("；")
            : `请求失败（${response.status}）`,
      );
    }
    return data as T;
  } catch (error) {
    if (
      controller.signal.aborted ||
      (error instanceof Error && error.name === "AbortError")
    )
      throw new Error("请求超时，操作结果尚不确定。请刷新后确认，勿重复提交。");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
