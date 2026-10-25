import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { api } from "./api";
import {
  ConfirmDialog,
  ManualDialog,
  Modal,
  SettingsDialog,
  UploadDialog,
} from "./Dialogs";

vi.mock("./api", () => ({ api: vi.fn() }));
const request = vi.mocked(api);
const settings = {
  mode: "mock",
  llm: {
    base_url: "https://example.org/v1",
    model: "test-model",
    api_key_configured: true,
    api_key_from_env: false,
    max_output_tokens: 2000,
  },
  deepseek: { api_key_configured: false, api_key_from_env: false },
  semantic_scholar: {
    api_key_configured: false,
    api_key_from_env: false,
    rate_per_second: 1,
  },
  budget: { per_entry: 12, per_task: 40 },
  cache: { metadata_days: 180, search_days: 30, negative_days: 7 },
};

beforeEach(() => {
  request.mockReset();
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = true;
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, "close", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = false;
    },
  });
});
afterEach(cleanup);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function formFor(button: HTMLElement) {
  return button.closest("form")!;
}

describe("native modal lifecycle", () => {
  test("Escape and outside click dismiss, while closing restores prior focus", () => {
    const trigger = document.createElement("button");
    document.body.append(trigger);
    trigger.focus();
    const close = vi.fn();
    const view = render(
      <Modal id="test-dialog" title="测试" onClose={close}>
        <input aria-label="内容" />
      </Modal>,
    );
    const dialog = screen.getByRole("dialog") as HTMLDialogElement;
    expect(dialog.open).toBe(true);
    screen.getByLabelText("内容").focus();
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(close).toHaveBeenCalledTimes(1);
    vi.spyOn(dialog, "getBoundingClientRect").mockReturnValue({
      left: 10,
      right: 50,
      top: 10,
      bottom: 50,
      width: 40,
      height: 40,
      x: 10,
      y: 10,
      toJSON: () => ({}),
    });
    fireEvent.click(dialog, { clientX: 30, clientY: 30 });
    expect(close).toHaveBeenCalledTimes(1);
    fireEvent.click(dialog, { clientX: 60, clientY: 30 });
    expect(close).toHaveBeenCalledTimes(2);
    view.unmount();
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });
  test("busy mutations block Escape dismissal and repeated confirmation only resolves once", () => {
    const close = vi.fn();
    const view = render(
      <Modal id="busy-dialog" title="处理" onClose={close} busy>
        <p>保存中</p>
      </Modal>,
    );
    fireEvent(
      screen.getByRole("dialog"),
      new Event("cancel", { cancelable: true }),
    );
    expect(close).not.toHaveBeenCalled();
    expect(
      (screen.getByRole("button", { name: "关闭" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    view.unmount();
    const confirmed = vi.fn();
    render(
      <ConfirmDialog
        title="确认"
        description="改变数据"
        onConfirm={confirmed}
        onClose={close}
      />,
    );
    const form = formFor(screen.getByRole("button", { name: "确认" }));
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(confirmed).toHaveBeenCalledTimes(1);
  });
});

describe("upload and manual entry", () => {
  test("drag/drop submits exact offline file and parent once despite repeated submission", async () => {
    const pending = deferred<{ id: string }>();
    request.mockReturnValue(pending.promise);
    const created = vi.fn(),
      close = vi.fn();
    render(
      <UploadDialog
        parentTaskId="previous-task"
        onClose={close}
        onCreated={created}
      />,
    );
    const file = new File(["@article{a,title={Example}}"], "sample.bib", {
      type: "text/plain",
    });
    fireEvent.drop(screen.getByLabelText("BibTeX 文件").closest("label")!, {
      dataTransfer: { files: [file] },
    });
    expect(screen.getByText("sample.bib")).toBeDefined();
    const form = formFor(screen.getByRole("button", { name: "开始离线演示" }));
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(request).toHaveBeenCalledTimes(1);
    const data = request.mock.calls[0][1]?.body as FormData;
    expect(data.get("file")).toBe(file);
    expect(data.get("mode")).toBe("mock");
    expect(data.get("parent_id")).toBe("previous-task");
    expect(data.get("cnki_mode")).toBe("off");
    await act(async () => {
      pending.resolve({ id: "new-task" });
    });
    expect(created).toHaveBeenCalledWith("new-task");
    expect(close).toHaveBeenCalledTimes(1);
  });
  test("invalid filenames and oversized files never create tasks; online mode is explicit", async () => {
    request.mockResolvedValue({ id: "live-task" });
    render(<UploadDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    const input = screen.getByLabelText("BibTeX 文件");
    const form = formFor(screen.getByRole("button", { name: "开始离线演示" }));
    fireEvent.change(input, {
      target: { files: [new File(["x"], "bad.txt")] },
    });
    fireEvent.submit(form);
    expect(screen.getByRole("alert").textContent).toBe("请选择 .bib 文件。");
    expect(request).not.toHaveBeenCalled();
    fireEvent.change(input, {
      target: {
        files: [new File([new Uint8Array(2 * 1024 * 1024 + 1)], "big.bib")],
      },
    });
    fireEvent.submit(form);
    expect(screen.getByRole("alert").textContent).toBe("文件不得超过 2 MB。");
    expect(request).not.toHaveBeenCalled();
    fireEvent.change(input, {
      target: { files: [new File(["@misc{a}"], "live.bib")] },
    });
    fireEvent.click(screen.getByRole("radio", { name: /在线核验/ }));
    fireEvent.click(screen.getByRole("button", { name: "开始在线核验" }));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect((request.mock.calls[0][1]?.body as FormData).get("mode")).toBe(
      "live",
    );
  });
  test("manual entry rejects credential links and preserves link-only format", async () => {
    request.mockResolvedValue({ id: "manual-task" });
    const created = vi.fn();
    render(
      <ManualDialog entryId="entry/1" onClose={vi.fn()} onCreated={created} />,
    );
    const source = screen.getByLabelText("来源链接");
    const form = formFor(screen.getByRole("button", { name: "保存人工证据" }));
    fireEvent.change(source, {
      target: { value: "https://user:secret@example.org/paper" },
    });
    fireEvent.submit(form);
    expect(request).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("不含凭据");
    fireEvent.change(source, {
      target: { value: "https://example.org/paper" },
    });
    fireEvent.submit(form);
    await waitFor(() => expect(created).toHaveBeenCalledWith("manual-task"));
    expect(request).toHaveBeenCalledWith("/api/entries/entry%2F1/manual", {
      method: "POST",
      body: {
        text: "",
        source_url: "https://example.org/paper",
        format: "text",
      },
    });
  });
});

describe("settings and live API boundaries", () => {
  test("saving sends only editable fields and discards typed secrets after success", async () => {
    request.mockImplementation(async (_path, options) =>
      options?.method === "PUT" ? {} : settings,
    );
    const notify = vi.fn();
    render(
      <SettingsDialog onClose={vi.fn()} notify={notify} confirm={vi.fn()} />,
    );
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "保存设置" }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    const key = screen.getAllByLabelText("API key")[0] as HTMLInputElement;
    fireEvent.change(key, { target: { value: "new-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => expect(notify).toHaveBeenCalledWith("设置已保存"));
    const write = request.mock.calls.find(
      ([, options]) => options?.method === "PUT",
    );
    expect(write?.[1]?.body).toEqual({
      llm: {
        base_url: "https://example.org/v1",
        model: "test-model",
        api_key: "new-secret",
      },
      semantic_scholar: { rate_per_second: 1 },
      budget: { per_entry: 12, per_task: 40 },
      cache: { metadata_days: 180, search_days: 30, negative_days: 7 },
    });
    await waitFor(() => expect(key.value).toBe(""));
  });
  test("closing and reopening discards an unsaved password", async () => {
    request.mockResolvedValue(settings);
    const props = { onClose: vi.fn(), notify: vi.fn(), confirm: vi.fn() };
    const view = render(<SettingsDialog {...props} />);
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "保存设置" }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    fireEvent.change(screen.getAllByLabelText("API key")[0], {
      target: { value: "unsaved-secret" },
    });
    fireEvent(
      screen.getByRole("dialog"),
      new Event("cancel", { cancelable: true }),
    );
    expect(props.onClose).toHaveBeenCalledTimes(1);
    view.unmount();
    render(<SettingsDialog {...props} />);
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "保存设置" }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    expect(
      (screen.getAllByLabelText("API key")[0] as HTMLInputElement).value,
    ).toBe("");
    expect(request.mock.calls.every(([, options]) => !options?.method)).toBe(
      true,
    );
  });
  test("connection tests require confirmation and use saved configuration, not typed secrets", async () => {
    request.mockImplementation(async (path) =>
      path === "/api/settings" ? settings : { ok: true, message: "已连通" },
    );
    const confirm = vi.fn().mockResolvedValue(false);
    render(
      <SettingsDialog onClose={vi.fn()} notify={vi.fn()} confirm={confirm} />,
    );
    const button = screen.getByRole("button", { name: "测试大模型连接" });
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.change(screen.getAllByLabelText("API key")[0], {
      target: { value: "unsaved-secret" },
    });
    fireEvent.click(button);
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(request).toHaveBeenCalledTimes(1);
    confirm.mockResolvedValue(true);
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByText("已连通")).toBeDefined());
    expect(request).toHaveBeenLastCalledWith("/api/settings/test/llm", {
      method: "POST",
    });
    expect(JSON.stringify(request.mock.calls)).not.toContain("unsaved-secret");
  });
});
