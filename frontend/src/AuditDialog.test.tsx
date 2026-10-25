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
import { AuditDialog, formatCost } from "./AuditDialog";
vi.mock("./api", () => ({ api: vi.fn() }));
const request = vi.mocked(api);
const response = {
  calls: [
    {
      id: 1,
      provider_id: "provider-a",
      model: "test-model",
      status: "error",
      attempt: 2,
      query: {
        messages: [{ role: "user", content: "<img src=x onerror=alert(1)>" }],
      },
      response: "partial response",
      error: "timeout",
      usage_metadata: { prompt_tokens: 10 },
      cost: { status: "unknown", amount: null },
      task_id: "task-a",
      entry_id: "entry-a",
    },
  ],
  total: 1,
  summary: {
    estimated_costs: [
      { currency: "USD", amount: 0.125 },
      { currency: "CNY", amount: 0.3 },
    ],
    unknown_cost_calls: 1,
    total_calls: 3,
  },
};
beforeEach(() => {
  request.mockReset();
  request.mockResolvedValue(response);
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
});
afterEach(cleanup);

test("unknown prices and missing usage never display a zero amount", () => {
  expect(formatCost()).toBe("未知");
  expect(formatCost({ status: "unknown", amount: null })).toBe("未知");
  expect(
    formatCost({ status: "estimated", amount: NaN, currency: "USD" }),
  ).toBe("未知");
  expect(formatCost({ status: "estimated", amount: 0, currency: "USD" })).toBe(
    "USD 0.00",
  );
  expect(formatCost({ status: "not_applicable", amount: null })).toBe("不适用");
});
test("audit preserves currency groups, renders text safely and filters scope", async () => {
  render(<AuditDialog taskId="task-a" onClose={vi.fn()} />);
  await screen.findByText("test-model");
  expect(screen.getByText("USD 0.125 + CNY 0.30")).toBeDefined();
  expect(screen.getByText("1 次费用未知")).toBeDefined();
  expect(screen.getByText("未知")).toBeDefined();
  expect(screen.getByText("partial response")).toBeDefined();
  expect(document.querySelector("img")).toBeNull();
  expect(request.mock.calls[0][0]).toContain("task_id=task-a");
  fireEvent.change(screen.getByLabelText("Provider ID"), {
    target: { value: "provider-a" },
  });
  fireEvent.click(screen.getByRole("button", { name: "筛选" }));
  await waitFor(() =>
    expect(request.mock.calls.at(-1)?.[0]).toContain("provider_id=provider-a"),
  );
});
test("late old task response cannot overwrite newer audit scope", async () => {
  let resolve!: (value: typeof response) => void;
  const pending = new Promise<typeof response>((done) => {
    resolve = done;
  });
  request.mockImplementation(async (path) =>
    path.includes("task_id=old")
      ? pending
      : { ...response, calls: [{ ...response.calls[0], model: "new-model" }] },
  );
  const view = render(<AuditDialog taskId="old" onClose={vi.fn()} />);
  view.rerender(<AuditDialog taskId="new" onClose={vi.fn()} />);
  await screen.findByText("new-model");
  await act(async () => {
    resolve(response);
  });
  expect(screen.queryByText("test-model")).toBeNull();
});
test("fetch failure is retryable and audit pagination never writes", async () => {
  request.mockRejectedValueOnce(new Error("暂时断线"));
  render(<AuditDialog onClose={vi.fn()} />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "刷新" }));
  await screen.findByText("test-model");
  expect(
    (screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(request.mock.calls.every(([, options]) => !options?.method)).toBe(
    true,
  );
});
