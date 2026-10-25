import { afterEach, expect, test, vi } from "vitest";
import { api } from "./api";
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test("JSON writes serialize once and multipart retains its boundary", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(
      async () => new Response('{"id":"saved"}', { status: 200 }),
    );
  vi.stubGlobal("fetch", fetcher);
  expect(
    await api("/api/settings", {
      method: "PUT",
      body: { llm: { model: "test" } },
    }),
  ).toEqual({ id: "saved" });
  expect(fetcher.mock.calls[0][1].body).toBe('{"llm":{"model":"test"}}');
  expect(fetcher.mock.calls[0][1].headers.get("Content-Type")).toBe(
    "application/json",
  );
  const form = new FormData();
  form.set("mode", "mock");
  await api("/api/tasks", { method: "POST", body: form });
  expect(fetcher.mock.calls[1][1].body).toBe(form);
  expect(fetcher.mock.calls[1][1].headers.has("Content-Type")).toBe(false);
});

test("errors preserve API detail and never automatically retry writes", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      new Response('{"detail":"Configuration locked"}', { status: 409 }),
    );
  vi.stubGlobal("fetch", fetcher);
  await expect(
    api("/api/settings", { method: "PUT", body: {} }),
  ).rejects.toThrow("Configuration locked");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

test("timed-out mutations report uncertainty and are not replayed", async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(
    (_path: string, options: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        options.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")),
        );
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const pending = api("/api/tasks", {
    method: "POST",
    body: {},
    timeout: 5,
  }).catch((error) => error);
  await vi.advanceTimersByTimeAsync(6);
  expect((await pending).message).toContain("操作结果尚不确定");
  expect(fetcher).toHaveBeenCalledTimes(1);
});
