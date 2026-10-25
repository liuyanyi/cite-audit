import { StrictMode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App, { EntryBadges, matchesEntry, type Task } from "./App";
import type { Entry } from "./EntryDetail";
import { api } from "./api";
vi.mock("./api", () => ({ api: vi.fn() }));
const mockedApi = vi.mocked(api);

const base: Entry = {
  id: "entry-a",
  citation_key: "alpha",
  entry_type: "article",
  status: "completed",
  fields: { title: "Alpha study", doi: "10.1000/alpha" },
  raw: "@article{alpha}",
  result: {
    identity_state: "unique",
    doi_state: "verified",
    field_state: "consistent",
  },
};
function task(id: string, status = "completed"): Task {
  return {
    id,
    filename: `${id}.bib`,
    mode: "mock",
    status,
    entries: [
      {
        ...base,
        id: `${id}-entry`,
        citation_key: id,
        fields: { ...base.fields, title: `${id} title` },
      },
    ],
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
beforeEach(() => {
  mockedApi.mockReset();
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("all filters keep DOI uncertainty distinct from absence and field agreement", () => {
  const unverified: Entry = {
    ...base,
    result: { ...base.result, doi_state: "unverified" },
  };
  expect(matchesEntry(base)).toBe(true);
  expect(matchesEntry(base, " ALPHA ")).toBe(true);
  expect(matchesEntry(base, "unrelated")).toBe(false);
  expect(matchesEntry(base, "", "missing_doi")).toBe(false);
  for (const filter of ["issues", "missing_doi", "insufficient"])
    expect(matchesEntry(unverified, "", filter)).toBe(true);
  expect(
    matchesEntry(
      { ...base, result: { ...base.result, doi_state: "conflict" } },
      "",
      "conflict",
    ),
  ).toBe(true);
  expect(
    matchesEntry(
      { ...base, result: { ...base.result, publication_state: "candidate" } },
      "",
      "upgrade",
    ),
  ).toBe(true);
  expect(matchesEntry({ ...base, status: "failed" }, "", "failed")).toBe(true);
  expect(matchesEntry({ ...base, is_chinese: true }, "", "chinese")).toBe(true);
  render(<EntryBadges entry={unverified} />);
  expect(screen.getByText("DOI 未核验")).toBeDefined();
});

test.each(["success", "failure"])(
  "newer task navigation wins over a stale %s response",
  async (outcome) => {
    const a = task("a"),
      b = task("b"),
      c = task("c");
    const pending = deferred<Task>();
    mockedApi.mockImplementation(async (path) => {
      if (path === "/api/tasks") return { tasks: [a, b, c] };
      if (path === "/api/tasks/b") return pending.promise;
      return path === "/api/tasks/c" ? c : a;
    });
    render(<App />);
    await screen.findByRole("heading", { name: "a.bib" });
    fireEvent.click(screen.getByRole("button", { name: /b\.bib/ }));
    fireEvent.click(screen.getByRole("button", { name: /c\.bib/ }));
    await screen.findByRole("heading", { name: "c.bib" });
    await act(async () => {
      if (outcome === "success") pending.resolve(b);
      else pending.reject(new Error("stale failure"));
    });
    expect(screen.getByRole("heading", { name: "c.bib" })).toBeDefined();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(localStorage.getItem("cite-audit.task")).toBe("c");
  },
);

test("poll refresh preserves selected entry and detail tab, then stops after unmount", async () => {
  const running = {
    ...task("a", "running"),
    entries: [
      base,
      {
        ...base,
        id: "second",
        citation_key: "second",
        fields: { title: "Second paper" },
      },
    ],
  };
  mockedApi.mockImplementation(async (path) =>
    path === "/api/tasks" ? { tasks: [running] } : running,
  );
  const view = render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
  await screen.findByRole("heading", { name: "a.bib" });
  fireEvent.click(screen.getByRole("button", { name: /second.*Second paper/ }));
  fireEvent.click(screen.getByRole("tab", { name: "原始条目" }));
  vi.useFakeTimers();
  // Visibility wake gives the mounted polling loop a controlled timer.
  fireEvent(document, new Event("visibilitychange"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2100);
  });
  expect(
    screen.getByRole("tab", { name: "原始条目" }).getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.getByRole("heading", { name: "Second paper" })).toBeDefined();
  view.unmount();
  const count = mockedApi.mock.calls.length;
  await vi.advanceTimersByTimeAsync(5000);
  expect(mockedApi).toHaveBeenCalledTimes(count);
  expect(mockedApi.mock.calls.some(([, options]) => options?.method)).toBe(
    false,
  );
});

test("deleting a captured task never clears a newer task selected before confirmation", async () => {
  const a = task("a"),
    b = task("b");
  let removed = false;
  mockedApi.mockImplementation(async (path, options) => {
    if (options?.method === "DELETE") {
      removed = true;
      return { deleted: true };
    }
    if (path === "/api/tasks") return { tasks: removed ? [b] : [a, b] };
    return path === "/api/tasks/b" ? b : a;
  });
  render(<App />);
  await screen.findByRole("heading", { name: "a.bib" });
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  fireEvent.click(screen.getByRole("button", { name: /b\.bib/ }));
  await screen.findByRole("heading", { name: "b.bib" });
  fireEvent.click(screen.getByRole("button", { name: "删除检查" }));
  await waitFor(() =>
    expect(
      mockedApi.mock.calls.some(
        ([path, options]) =>
          path === "/api/tasks/a" && options?.method === "DELETE",
      ),
    ).toBe(true),
  );
  await act(async () => {});
  expect(screen.getByRole("heading", { name: "b.bib" })).toBeDefined();
  expect(localStorage.getItem("cite-audit.task")).toBe("b");
});

test("cancelling a destructive confirmation sends no mutation", async () => {
  const a = task("a");
  mockedApi.mockImplementation(async (path) =>
    path === "/api/tasks" ? { tasks: [a] } : a,
  );
  render(<App />);
  await screen.findByRole("heading", { name: "a.bib" });
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  fireEvent(
    screen.getByRole("dialog"),
    new Event("cancel", { cancelable: true }),
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(mockedApi.mock.calls.some(([, options]) => options?.method)).toBe(
    false,
  );
});
