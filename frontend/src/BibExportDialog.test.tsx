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
import { BibExportDialog } from "./BibExportDialog";
vi.mock("./api", () => ({ api: vi.fn() }));
const request = vi.mocked(api);
const preview = {
  task_id: "task",
  filename: "input.bib",
  warnings: [],
  entries: [
    {
      entry_id: "entry",
      citation_key: "Key",
      eligible: true,
      original: "@misc{Key}",
      current: {
        bibtex: "@article{Key}",
        changes: [
          {
            field: "entry_type",
            original: "misc",
            suggested: "article",
            evidence_ids: ["acl"],
          },
          { field: "year", suggested: "2024" },
        ],
      },
      publication: {
        bibtex: "@article{Key,year={2025}}",
        changes: [{ field: "year", suggested: "2025" }],
      },
    },
    {
      entry_id: "broken",
      citation_key: "Broken",
      eligible: false,
      reason: "重复 citation key",
      original: "@misc{Broken}",
      current: null,
      publication: null,
    },
  ],
};
const output = {
  filename: "revised.bib",
  bibtex: "@article{Key}",
  changes: [],
  warnings: [],
  source_sha256: "digest",
};
beforeEach(() => {
  request.mockReset();
  request.mockImplementation(async (path) =>
    path.endsWith("export-preview") ? preview : output,
  );
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
});
afterEach(cleanup);
function show(confirm = vi.fn().mockResolvedValue(true)) {
  return render(
    <BibExportDialog
      taskId="task"
      onClose={vi.fn()}
      onInspect={vi.fn()}
      confirm={confirm}
    />,
  );
}
test("preserves all entries by default and exports only selected evidenced fields", async () => {
  show();
  await screen.findByText("重复 citation key");
  expect((screen.getByLabelText("保留原文") as HTMLInputElement).checked).toBe(
    true,
  );
  fireEvent.click(screen.getByRole("button", { name: "生成导出" }));
  await screen.findByText("导出已生成");
  expect(request).toHaveBeenLastCalledWith("/api/tasks/task/export", {
    method: "POST",
    body: { selections: [] },
  });
  fireEvent.click(screen.getByLabelText("Key: year"));
  expect(screen.queryByText("导出已生成")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "生成导出" }));
  await screen.findByText("导出已生成");
  expect(request).toHaveBeenLastCalledWith("/api/tasks/task/export", {
    method: "POST",
    body: {
      selections: [{ entry_id: "entry", choice: "current", fields: ["year"] }],
    },
  });
});
test("publication adoption requires explicit confirmation and stays atomic", async () => {
  const confirm = vi
    .fn()
    .mockResolvedValueOnce(false)
    .mockResolvedValueOnce(true);
  show(confirm);
  await screen.findByLabelText("改用正式版");
  fireEvent.click(screen.getByLabelText("改用正式版"));
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
  expect((screen.getByLabelText("保留原文") as HTMLInputElement).checked).toBe(
    true,
  );
  fireEvent.click(screen.getByLabelText("改用正式版"));
  await waitFor(() =>
    expect(
      (screen.getByLabelText("改用正式版") as HTMLInputElement).checked,
    ).toBe(true),
  );
  fireEvent.click(screen.getByRole("button", { name: "生成导出" }));
  await screen.findByText("导出已生成");
  expect(request).toHaveBeenLastCalledWith("/api/tasks/task/export", {
    method: "POST",
    body: { selections: [{ entry_id: "entry", choice: "publication" }] },
  });
});
test("export failure retains selection and duplicate submits are blocked", async () => {
  let reject!: (reason: Error) => void;
  request.mockImplementation(async (path) =>
    path.endsWith("export-preview")
      ? preview
      : new Promise((_, fail) => {
          reject = fail;
        }),
  );
  show();
  await screen.findByLabelText("Key: year");
  fireEvent.click(screen.getByLabelText("Key: year"));
  const button = screen.getByRole("button", { name: "生成导出" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(
    request.mock.calls.filter(([path]) => path.endsWith("/export")),
  ).toHaveLength(1);
  await act(async () => reject(new Error("生成失败")));
  expect(screen.getByRole("alert").textContent).toContain("生成失败");
  expect((screen.getByLabelText("Key: year") as HTMLInputElement).checked).toBe(
    true,
  );
});

test("late export responses cannot leak into a newly opened task", async () => {
  let resolve!: (value: typeof output) => void;
  request.mockImplementation(async (path) =>
    path.endsWith("export-preview")
      ? preview
      : new Promise((done) => {
          resolve = done;
        }),
  );
  const confirm = vi.fn().mockResolvedValue(true);
  const view = show(confirm);
  await screen.findByLabelText("保留原文");
  fireEvent.click(screen.getByRole("button", { name: "生成导出" }));
  view.rerender(
    <BibExportDialog
      taskId="new"
      onClose={vi.fn()}
      onInspect={vi.fn()}
      confirm={confirm}
    />,
  );
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith("/api/tasks/new/export-preview"),
  );
  await act(async () => resolve(output));
  expect(screen.queryByText("导出已生成")).toBeNull();
});
test("downloads use server-generated Bib and a separate provenance report", async () => {
  const create = vi.fn().mockReturnValue("blob:export");
  vi.stubGlobal(
    "URL",
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }),
  );
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  show();
  await screen.findByLabelText("保留原文");
  fireEvent.click(screen.getByRole("button", { name: "生成导出" }));
  fireEvent.click(await screen.findByRole("button", { name: "下载完整 Bib" }));
  fireEvent.click(screen.getByRole("button", { name: "下载修改清单" }));
  expect(create).toHaveBeenCalledTimes(2);
  expect(click).toHaveBeenCalledTimes(2);
  expect((create.mock.calls[0][0] as Blob).type).toBe(
    "application/x-bibtex;charset=utf-8",
  );
  expect((create.mock.calls[1][0] as Blob).type).toBe(
    "application/json;charset=utf-8",
  );
  click.mockRestore();
  vi.unstubAllGlobals();
});

test("field presets change only current suggestions and never select publication upgrades", async () => {
  const entry = preview.entries[0];
  const enhanced = {
    ...preview,
    entries: [
      {
        ...entry,
        current: {
          ...entry.current!,
          changes: [
            ...entry.current!.changes,
            { field: "abstract", suggested: "Extra abstract" },
            { field: "keywords", suggested: "Extra keywords" },
          ],
        },
      },
    ],
  };
  request.mockImplementation(async (path) =>
    path.endsWith("export-preview") ? enhanced : output,
  );
  show();
  await screen.findByLabelText("Key: abstract");
  fireEvent.click(screen.getByRole("button", { name: "引用必需字段" }));
  expect((screen.getByLabelText("Key: year") as HTMLInputElement).checked).toBe(
    true,
  );
  expect(
    (screen.getByLabelText("Key: abstract") as HTMLInputElement).checked,
  ).toBe(false);
  expect(
    (screen.getByLabelText("Key: keywords") as HTMLInputElement).checked,
  ).toBe(false);
  expect(
    (screen.getByLabelText("改用正式版") as HTMLInputElement).checked,
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "丰富信息" }));
  expect(
    (screen.getByLabelText("Key: abstract") as HTMLInputElement).checked,
  ).toBe(true);
  expect(
    (screen.getByLabelText("Key: keywords") as HTMLInputElement).checked,
  ).toBe(true);
  fireEvent.click(screen.getByLabelText("改用正式版"));
  await waitFor(() =>
    expect(
      (screen.getByLabelText("改用正式版") as HTMLInputElement).checked,
    ).toBe(true),
  );
  fireEvent.click(screen.getByRole("button", { name: "引用必需字段" }));
  expect(
    (screen.getByLabelText("改用正式版") as HTMLInputElement).checked,
  ).toBe(true);
});
