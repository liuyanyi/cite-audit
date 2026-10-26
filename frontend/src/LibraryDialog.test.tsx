import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api } from "./api";
import { LibraryDialog } from "./LibraryDialog";
vi.mock("./api", () => ({ api: vi.fn() }));
const request = vi.mocked(api);
const item = {
  id: "one",
  identity_key: "doi:one",
  status: "candidate",
  mode: "mock",
  version: "preprint",
  fields: { title: "Paper one" },
  citation_keys: ["Key"],
};
const detail = {
  ...item,
  snapshots: [
    {
      id: "s1",
      provider: "ACL",
      cache_state: "stale",
      revalidation_error: "timeout",
      last_checked_at: "2026-01-01",
      fields: { title: "Paper one" },
    },
  ],
  decisions: [
    { task_id: "old-task", entry_id: "entry", identity_state: "confirmed" },
  ],
};
beforeEach(() => {
  request.mockReset();
  request.mockImplementation(async (path) =>
    path.includes("?") ? { items: [item], total: 1 } : detail,
  );
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
});
afterEach(cleanup);
test("shows persistent source snapshots, mock distinction and failed revalidation", async () => {
  const open = vi.fn();
  render(<LibraryDialog onClose={vi.fn()} onOpenTask={open} />);
  await screen.findByText("历史证据 · 复核失败");
  expect(
    screen.getByText("离线演示数据，不可作为真实文献依据。"),
  ).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "打开检查" }));
  expect(open).toHaveBeenCalledWith("old-task");
  fireEvent.change(screen.getByLabelText("来源模式"), {
    target: { value: "live" },
  });
  await waitFor(() =>
    expect(
      request.mock.calls.some(([path]) => path.includes("mode=live")),
    ).toBe(true),
  );
  fireEvent.change(screen.getByLabelText("搜索文献库"), {
    target: { value: "ACL" },
  });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
  await waitFor(() =>
    expect(request.mock.calls.some(([path]) => path.includes("q=ACL"))).toBe(
      true,
    ),
  );
});
test("a late source detail cannot replace a newer selected record", async () => {
  let resolve!: (value: typeof detail) => void;
  const pending = new Promise<typeof detail>((done) => {
    resolve = done;
  });
  request.mockImplementation(async (path) =>
    path.includes("?")
      ? {
          items: [item, { ...item, id: "two", fields: { title: "Paper two" } }],
          total: 2,
        }
      : path.endsWith("one")
        ? pending
        : {
            ...detail,
            id: "two",
            fields: { title: "Paper two" },
            snapshots: [],
          },
  );
  render(<LibraryDialog onClose={vi.fn()} onOpenTask={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /Paper two/ }));
  await screen.findByText("暂无来源快照。");
  await act(async () => resolve(detail));
  expect(screen.queryByText("历史证据 · 复核失败")).toBeNull();
});
