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
import { SettingsDialog, UploadDialog } from "./Dialogs";
import { modelPayload, type ModelConfigurationSet } from "./ModelSettings";
vi.mock("./api", () => ({ api: vi.fn() }));
const request = vi.mocked(api);
const configuration: ModelConfigurationSet = {
  llm_providers: [
    {
      id: "provider-a",
      name: "官方",
      client_type: "openai-chat",
      base_url: "https://api.example.com/v1",
      api_key_configured: true,
      api_key_from_env: false,
    },
  ],
  llm_models: [
    {
      id: "model-a",
      name: "核验模型 A",
      provider_id: "provider-a",
      model: "model-1",
      temperature: 0,
      timeout: 45,
      max_output_tokens: 1500,
      max_retries: 2,
    },
  ],
  default_model_id: "model-a",
};
const settings = {
  ...configuration,
  deepseek: {},
  semantic_scholar: { rate_per_second: 1 },
  budget: { per_entry: 12, per_task: 40 },
  cache: { metadata_days: 180, search_days: 30, negative_days: 7 },
};
const catalog = {
  currency: "USD",
  models: [
    {
      model: "model-1",
      base_url: "https://api.example.com/v1",
      client: "openai-chat",
      pricing: {
        cached_tokens: 0.2,
        prompt_tokens: 1,
        thoughts_tokens: 3,
        response_tokens: 3,
      },
    },
  ],
};
function renderSettings(confirm = vi.fn().mockResolvedValue(true)) {
  const props = { confirm, onClose: vi.fn(), notify: vi.fn() };
  return { ...render(<SettingsDialog {...props} />), ...props };
}
beforeEach(() => {
  request.mockReset();
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  request.mockImplementation(async (path, options) => {
    if (options?.method === "PUT") return { ok: true };
    if (path === "/api/models/catalog") return catalog;
    if (path === "/api/settings") return settings;
    return { ok: true, message: "模型已连通" };
  });
});
afterEach(cleanup);

test("provider settings render catalog pricing and emit only writable fields", async () => {
  const view = renderSettings();
  await screen.findByDisplayValue("核验模型 A");
  await screen.findByText(/输入 1.*USD \/ 百万 token/);
  fireEvent.change(screen.getAllByLabelText("API key")[0], {
    target: { value: "new-secret" },
  });
  fireEvent.change(screen.getByLabelText("显示名称"), {
    target: { value: "修改后的模型" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() => expect(view.notify).toHaveBeenCalledWith("设置已保存"));
  const payload = request.mock.calls.find(
    ([, options]) => options?.method === "PUT",
  )?.[1]?.body as Record<string, unknown>;
  expect(payload.llm_providers).toEqual([
    {
      id: "provider-a",
      name: "官方",
      client_type: "openai-chat",
      base_url: "https://api.example.com/v1",
      api_key: "new-secret",
    },
  ]);
  expect((payload.llm_models as Array<{ name: string }>)[0].name).toBe(
    "修改后的模型",
  );
  expect(payload.default_model_id).toBe("model-a");
  expect(payload.llm).toBeUndefined();
  await waitFor(() =>
    expect(
      (screen.getAllByLabelText("API key")[0] as HTMLInputElement).value,
    ).toBe(""),
  );
});

test("provider/model edits stay local until save and removal requires confirmation", async () => {
  const confirm = vi.fn().mockResolvedValue(false);
  renderSettings(confirm);
  await screen.findByDisplayValue("核验模型 A");
  fireEvent.click(screen.getByRole("button", { name: "移除 provider" }));
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
  expect(screen.getByDisplayValue("官方")).toBeDefined();
  confirm.mockResolvedValue(true);
  fireEvent.click(screen.getByRole("button", { name: "移除 provider" }));
  await waitFor(() => expect(screen.queryByDisplayValue("官方")).toBeNull());
  expect(request.mock.calls.some(([, options]) => !!options?.method)).toBe(
    false,
  );
  fireEvent.click(screen.getByRole("button", { name: "添加 provider" }));
  fireEvent.change(screen.getByLabelText("名称"), {
    target: { value: "新的服务" },
  });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://new.example.com/v1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "添加模型" }));
  fireEvent.change(screen.getByLabelText("模型 ID"), {
    target: { value: "custom-model" },
  });
  expect(screen.getByText(/MMSP 未匹配到价格/)).toBeDefined();
  expect(
    (screen.getByRole("button", { name: "测试模型连接" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() =>
    expect(
      request.mock.calls.some(([, options]) => options?.method === "PUT"),
    ).toBe(true),
  );
});

test("saved model tests require confirmation and unsaved endpoint/key disables tests", async () => {
  const confirm = vi.fn().mockResolvedValue(false);
  renderSettings(confirm);
  const button = await screen.findByRole("button", { name: "测试模型连接" });
  fireEvent.click(button);
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
  expect(
    request.mock.calls.some(([, options]) => options?.method === "POST"),
  ).toBe(false);
  confirm.mockResolvedValue(true);
  fireEvent.click(button);
  await screen.findByText("模型已连通");
  expect(request).toHaveBeenCalledWith("/api/settings/test/model/model-a", {
    method: "POST",
  });
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://other.example.com" },
  });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://api.example.com/v1" },
  });
  fireEvent.change(screen.getAllByLabelText("API key")[0], {
    target: { value: "unsaved-secret" },
  });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  expect(JSON.stringify(request.mock.calls)).not.toContain("unsaved-secret");
});

test("key removal sends null, blank preserves, and closing discards unsaved keys", async () => {
  const view = renderSettings();
  await screen.findByDisplayValue("核验模型 A");
  fireEvent.click(screen.getByRole("button", { name: "移除已保存密钥" }));
  await screen.findByText("保存后移除密钥");
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() => expect(view.notify).toHaveBeenCalled());
  const payload = request.mock.calls.find(
    ([, options]) => options?.method === "PUT",
  )?.[1]?.body as ModelConfigurationSet;
  expect(payload.llm_providers[0].api_key).toBeNull();
  fireEvent.change(screen.getAllByLabelText("API key")[0], {
    target: { value: "discard-this" },
  });
  view.unmount();
  renderSettings();
  await screen.findByDisplayValue("核验模型 A");
  expect(
    (screen.getAllByLabelText("API key")[0] as HTMLInputElement).value,
  ).toBe("");
  expect(modelPayload(configuration).llm_providers[0].api_key).toBe("");
});

test("online upload waits for settings and sends selected model without calling it", async () => {
  let resolve!: (value: typeof settings) => void;
  const pending = new Promise<typeof settings>((done) => {
    resolve = done;
  });
  request.mockImplementation(async (path) =>
    path === "/api/settings" ? pending : { id: "task-new" },
  );
  const created = vi.fn();
  render(<UploadDialog onClose={vi.fn()} onCreated={created} />);
  fireEvent.change(screen.getByLabelText("BibTeX 文件"), {
    target: { files: [new File(["@misc{x}"], "sample.bib")] },
  });
  fireEvent.click(screen.getByRole("radio", { name: /在线核验/ }));
  expect(
    (screen.getByRole("button", { name: "开始在线核验" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await act(async () => {
    resolve(settings);
  });
  fireEvent.change(screen.getByLabelText("核验模型"), {
    target: { value: "model-a" },
  });
  fireEvent.click(screen.getByRole("button", { name: "开始在线核验" }));
  await waitFor(() => expect(created).toHaveBeenCalledWith("task-new"));
  const payload = request.mock.calls.find(
    ([path]) => path === "/api/tasks",
  )?.[1]?.body as FormData;
  expect(payload.get("model_id")).toBe("model-a");
  expect(payload.get("mode")).toBe("live");
  expect(request.mock.calls.some(([path]) => path.includes("/test/"))).toBe(
    false,
  );
});

test("catalog accepts backend protocol compatibility but ambiguous prices remain unknown", async () => {
  const compatible = {
    ...catalog.models[0],
    client: "openai-official",
    supported_client_types: ["openai-chat", "openai-responses"],
  };
  request.mockImplementation(async (path) =>
    path === "/api/models/catalog"
      ? { ...catalog, models: [compatible] }
      : settings,
  );
  const view = renderSettings();
  await screen.findByText(/输入 1/);
  view.unmount();
  request.mockImplementation(async (path) =>
    path === "/api/models/catalog"
      ? {
          ...catalog,
          models: [
            compatible,
            {
              ...compatible,
              pricing: { ...compatible.pricing, prompt_tokens: 99 },
            },
          ],
        }
      : settings,
  );
  renderSettings();
  await screen.findByText("MMSP 未匹配到价格，费用将标记为未知。");
});
