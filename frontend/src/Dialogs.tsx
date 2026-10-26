import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { api } from "./api";
import {
  ModelSettings,
  emptyModels,
  modelPayload,
  modelLabel,
  type ModelConfiguration,
  type ModelConfigurationSet,
  type ModelCatalog,
} from "./ModelSettings";

type Created = (id: string) => void | Promise<void>;
type Confirm = (
  title: string,
  description: string,
  label?: string,
) => Promise<boolean>;
type Notify = (message: string, error?: boolean) => void;
type TaskResponse = { id?: string; task?: { id: string } };
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试。";
function taskId(response: TaskResponse) {
  const id = response.id ?? response.task?.id;
  if (!id)
    throw new Error(
      "操作已返回，但未收到任务编号。请刷新检查记录，勿重复提交。",
    );
  return id;
}
function safeURL(value: string) {
  try {
    const url = new URL(value);
    return (
      ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
    );
  } catch {
    return false;
  }
}

export function Modal({
  id,
  title,
  className = "",
  onClose,
  children,
  busy = false,
}: {
  id: string;
  title: string;
  className?: string;
  onClose: () => void;
  children: ReactNode;
  busy?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement;
    element.showModal();
    return () => {
      element.close();
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      id={id}
      className={`dialog ${className}`}
      aria-labelledby={`${id}-title`}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget || busy) return;
        const box = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < box.left ||
          event.clientX > box.right ||
          event.clientY < box.top ||
          event.clientY > box.bottom
        )
          onClose();
      }}
    >
      <div className="dialog-heading">
        <h2 id={`${id}-title`}>{title}</h2>
        <button
          className="icon-button"
          type="button"
          onClick={onClose}
          disabled={busy}
          aria-label="关闭"
        >
          ×
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function ConfirmDialog({
  title,
  description,
  label = "确认",
  onConfirm,
  onClose,
}: {
  title: string;
  description: string;
  label?: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const submitted = useRef(false);
  return (
    <Modal
      id="confirm-dialog"
      title={title}
      className="confirm-dialog"
      onClose={onClose}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!submitted.current) {
            submitted.current = true;
            onConfirm();
          }
        }}
      >
        <p className="confirm-description">{description}</p>
        <div className="dialog-footer">
          <button
            type="button"
            className="button quiet"
            onClick={onClose}
            autoFocus
          >
            取消
          </button>
          <button className="button primary" type="submit">
            {label}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function UploadDialog({
  parentTaskId,
  onClose,
  onCreated,
}: {
  parentTaskId?: string;
  onClose: () => void;
  onCreated: Created;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState("mock");
  const [modelSettings, setModelSettings] =
    useState<ModelConfigurationSet | null>(null);
  const [modelId, setModelId] = useState("");
  const [modelsError, setModelsError] = useState("");
  const [modelsReload, setModelsReload] = useState(0);
  useEffect(() => {
    if (mode !== "live") return;
    let active = true;
    setModelSettings(null);
    setModelsError("");
    setModelId("");
    api<Record<string, unknown>>("/api/settings")
      .then((result) => {
        if (!active) return;
        setModelSettings({
          llm_providers: Array.isArray(result.llm_providers)
            ? (result.llm_providers as ModelConfigurationSet["llm_providers"])
            : [],
          llm_models: Array.isArray(result.llm_models)
            ? (result.llm_models as ModelConfiguration[])
            : [],
          default_model_id:
            typeof result.default_model_id === "string"
              ? result.default_model_id
              : "",
        });
      })
      .catch((failure) => {
        if (active) setModelsError(errorText(failure));
      });
    return () => {
      active = false;
    };
  }, [mode, modelsReload]);

  const [cnki, setCnki] = useState("off");
  const [linkParent, setLinkParent] = useState(!!parentTaskId);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (pending.current || (mode === "live" && !modelSettings)) return;
    if (!file) {
      setError("请选择 .bib 文件。");
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      setError("文件不得超过 2 MB。");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".bib")) {
      setError("请选择 .bib 文件。");
      return;
    }
    const data = new FormData();
    data.set("file", file);
    data.set("mode", mode);
    data.set("cnki_mode", cnki);
    if (mode === "live" && modelId) data.set("model_id", modelId);
    if (parentTaskId && linkParent) data.set("parent_id", parentTaskId);
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api<TaskResponse>("/api/tasks", {
        method: "POST",
        body: data,
      });
      const id = taskId(result);
      onClose();
      await onCreated(id);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  return (
    <Modal
      id="upload-dialog"
      title="新建检查"
      className="upload-dialog"
      onClose={onClose}
      busy={busy}
    >
      <form onSubmit={submit}>
        <label
          className={`drop-zone${dragging ? " dragging" : ""}`}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            if (!busy && event.dataTransfer.files[0]) {
              setFile(event.dataTransfer.files[0]);
              setError("");
            }
          }}
        >
          <input
            name="file"
            type="file"
            accept=".bib,text/plain,application/x-bibtex"
            disabled={busy}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setError("");
            }}
            aria-label="BibTeX 文件"
          />
          <strong>{file?.name || "选择或拖入 .bib 文件"}</strong>
          <span>只检查，不改写原文件</span>
        </label>
        <fieldset className="mode-choice" disabled={busy}>
          <legend>检查方式</legend>
          <label>
            <input
              type="radio"
              name="mode"
              value="mock"
              checked={mode === "mock"}
              onChange={() => setMode("mock")}
            />
            <span>
              <strong>离线演示</strong>
              <small>模拟证据，不作真实核验</small>
            </span>
          </label>
          <label>
            <input
              type="radio"
              name="mode"
              value="live"
              checked={mode === "live"}
              onChange={() => setMode("live")}
            />
            <span>
              <strong>在线核验</strong>
              <small>向已配置来源发送必要文献字段，可能计费</small>
            </span>
          </label>
        </fieldset>
        {mode === "live" && (
          <div className="upload-model">
            {modelSettings ? (
              <label>
                核验模型
                <select
                  value={modelId}
                  onChange={(event) => setModelId(event.target.value)}
                  disabled={busy}
                >
                  <option value="">
                    默认：
                    {modelSettings.llm_models.find(
                      (model) =>
                        model.id === modelSettings.default_model_id &&
                        !!model.model.trim(),
                    )
                      ? modelLabel(
                          modelSettings.llm_models.find(
                            (model) =>
                              model.id === modelSettings.default_model_id,
                          )!,
                          modelSettings.llm_providers,
                        )
                      : "不使用大模型"}
                  </option>
                  {modelSettings.llm_models
                    .filter((model) => !!model.model.trim())
                    .map((model) => (
                      <option key={model.id} value={model.id}>
                        {modelLabel(model, modelSettings.llm_providers)}
                      </option>
                    ))}
                </select>
              </label>
            ) : modelsError ? (
              <div className="inline-error" role="alert">
                {modelsError}
                <button
                  type="button"
                  className="button small"
                  onClick={() => setModelsReload((value) => value + 1)}
                >
                  重新加载模型
                </button>
              </div>
            ) : (
              <p role="status">加载模型配置中…</p>
            )}
          </div>
        )}
        <label className="field-label" htmlFor="cnki-mode">
          CNKI 公开网页补充检索
        </label>
        <select
          id="cnki-mode"
          value={cnki}
          onChange={(event) => setCnki(event.target.value)}
          disabled={busy}
        >
          <option value="off">关闭</option>
          <option value="chinese">仅中文条目</option>
          <option value="all">全部条目</option>
        </select>
        {parentTaskId && (
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={linkParent}
              onChange={(event) => setLinkParent(event.target.checked)}
              disabled={busy}
            />
            作为当前任务的增量复查
          </label>
        )}
        <div className="inline-error" role="alert">
          {error}
        </div>
        <div className="dialog-footer">
          <button
            type="button"
            className="button quiet"
            onClick={onClose}
            disabled={busy}
          >
            取消
          </button>
          <button
            className="button primary"
            type="submit"
            disabled={busy || (mode === "live" && !modelSettings)}
          >
            {busy
              ? "创建中…"
              : mode === "live"
                ? "开始在线核验"
                : "开始离线演示"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function ManualDialog({
  entryId,
  onClose,
  onCreated,
}: {
  entryId: string;
  onClose: () => void;
  onCreated: Created;
}) {
  const [text, setText] = useState("");
  const [sourceURL, setSourceURL] = useState("");
  const [format, setFormat] = useState("bibtex");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const pending = useRef(false);
  const readSequence = useRef(0);
  useEffect(
    () => () => {
      readSequence.current++;
    },
    [],
  );
  async function readFile(file?: File) {
    if (!file) return;
    const sequence = ++readSequence.current;
    if (file.size > 200000) {
      setReading(false);
      setError("题录文件不得超过 200 KB。");
      return;
    }
    setReading(true);
    setError("");
    try {
      const value = await file.text();
      if (sequence === readSequence.current) {
        setText(value);
        setFormat(
          file.name.toLowerCase().endsWith(".ris")
            ? "ris"
            : file.name.toLowerCase().endsWith(".bib")
              ? "bibtex"
              : "text",
        );
      }
    } catch {
      if (sequence === readSequence.current) setError("无法读取题录文件。");
    } finally {
      if (sequence === readSequence.current) setReading(false);
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current || reading) return;
    if (!text.trim() && !sourceURL.trim()) {
      setError("请提供题录文本或来源链接。");
      return;
    }
    if (sourceURL && !safeURL(sourceURL)) {
      setError("仅支持不含凭据的 HTTP(S) 来源链接。");
      return;
    }
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api<TaskResponse>(
        `/api/entries/${encodeURIComponent(entryId)}/manual`,
        {
          method: "POST",
          body: {
            text,
            source_url: sourceURL,
            format: text.trim() ? format : "text",
          },
        },
      );
      const id = taskId(result);
      onClose();
      await onCreated(id);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <Modal
      id="manual-dialog"
      title="补充人工题录"
      onClose={onClose}
      busy={busy}
    >
      <form onSubmit={submit}>
        <p className="notice">人工导入保留为独立证据，不代表已在线核验。</p>
        <label>
          来源链接
          <input
            name="source_url"
            type="url"
            placeholder="https://…"
            value={sourceURL}
            onChange={(event) => setSourceURL(event.target.value)}
            disabled={busy}
          />
        </label>
        <div className="form-columns">
          <label>
            格式
            <select
              name="format"
              value={format}
              onChange={(event) => setFormat(event.target.value)}
              disabled={busy}
            >
              <option value="bibtex">BibTeX</option>
              <option value="ris">RIS</option>
              <option value="text">结构化文本 / 仅链接</option>
            </select>
          </label>
          <label className="manual-file-label">
            导入文件
            <input
              type="file"
              accept=".bib,.ris,.txt,text/plain"
              disabled={busy}
              onChange={(event) => void readFile(event.target.files?.[0])}
            />
          </label>
        </div>
        <label>
          题录文本
          <textarea
            name="text"
            rows={9}
            placeholder="粘贴单条 BibTeX / RIS，或每行“字段: 值”的文本。也可仅填写详情链接。"
            value={text}
            onChange={(event) => {
              readSequence.current++;
              setReading(false);
              setText(event.target.value);
            }}
            disabled={busy}
          />
        </label>
        <div className="inline-error" role="alert">
          {error}
        </div>
        <div className="dialog-footer">
          <button
            type="button"
            className="button quiet"
            onClick={onClose}
            disabled={busy}
          >
            取消
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={busy || reading}
          >
            {busy ? "保存中…" : "保存人工证据"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

type SettingGroup = Record<string, string | number | boolean | null>;
type Settings = Record<string, unknown>;
function settingGroup(settings: Settings | null, name: string): SettingGroup {
  const group = settings?.[name];
  return group && typeof group === "object" && !Array.isArray(group)
    ? (group as SettingGroup)
    : {};
}
const editableFields = [
  "llm.base_url",
  "llm.model",
  "semantic_scholar.rate_per_second",
  "budget.per_entry",
  "budget.per_task",
  "cache.metadata_days",
  "cache.search_days",
  "cache.negative_days",
];
type SettingPayload = Record<string, Record<string, string | number | null>>;
const providerNames: Record<string, string> = {
  llm: "大模型",
  deepseek: "DeepSeek",
  semantic_scholar: "Semantic Scholar",
  crossref: "Crossref",
  arxiv: "arXiv",
};

export function SettingsDialog({
  onClose,
  notify,
  confirm,
}: {
  onClose: () => void;
  notify: Notify;
  confirm: Confirm;
}) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [models, setModels] = useState<ModelConfigurationSet>(emptyModels);
  const [savedModels, setSavedModels] =
    useState<ModelConfigurationSet>(emptyModels);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const hasProviders = Array.isArray(settings?.llm_providers);

  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tests, setTests] = useState<
    Record<string, { text: string; ok?: boolean }>
  >({});
  const mounted = useRef(true);
  const pending = useRef(false);
  const prefix = useId();
  function applySettings(result: Settings) {
    if (!mounted.current) return;
    setSettings(result);
    if (Array.isArray(result.llm_providers)) {
      const configured: ModelConfigurationSet = {
        llm_providers: (
          result.llm_providers as ModelConfigurationSet["llm_providers"]
        ).map((provider) => ({ ...provider, api_key: "" })),
        llm_models: Array.isArray(result.llm_models)
          ? (result.llm_models as ModelConfiguration[])
          : [],
        default_model_id:
          typeof result.default_model_id === "string"
            ? result.default_model_id
            : "",
      };
      setModels(configured);
      setSavedModels(configured);
    }
    const next: Record<string, string> = {};
    for (const field of editableFields) {
      const [group, name] = field.split(".");
      const value = settingGroup(result, group)[name];
      next[field] = value == null ? "" : String(value);
    }
    setValues(next);
  }
  useEffect(() => {
    mounted.current = true;
    let active = true;
    api<Settings>("/api/settings")
      .then((result) => {
        if (active) {
          applySettings(result);
          if (Array.isArray(result.llm_providers)) {
            void api<ModelCatalog>("/api/models/catalog")
              .then((value) => {
                if (active && Array.isArray(value.models)) setCatalog(value);
              })
              .catch(() => {
                /* Unknown prices remain explicitly unknown. */
              });
          }
        }
      })
      .catch((failure) => {
        if (active) setError(errorText(failure));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      mounted.current = false;
    };
  }, []);
  async function run(operation: () => Promise<void>) {
    if (pending.current || !mounted.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (failure) {
      if (mounted.current) setError(errorText(failure));
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      const payload: SettingPayload = {};
      for (const [key, value] of Object.entries(values)) {
        if (!value.trim() || (hasProviders && key.startsWith("llm."))) continue;
        const [group, name] = key.split(".");
        (payload[group] ??= {})[name] =
          ["budget", "cache"].includes(group) || name === "rate_per_second"
            ? Number(value)
            : value.trim();
      }
      await api("/api/settings", {
        method: "PUT",
        body: { ...payload, ...(hasProviders ? modelPayload(models) : {}) },
      });
      // Never retain typed secrets after a successful save, including if the reload fails.
      if (mounted.current)
        setValues((current) =>
          Object.fromEntries(
            Object.entries(current).filter(
              ([name]) => !name.endsWith(".api_key"),
            ),
          ),
        );
      if (mounted.current)
        setModels((current) => ({
          ...current,
          llm_providers: current.llm_providers.map((provider) => ({
            ...provider,
            api_key: "",
          })),
        }));
      notify("设置已保存");
      applySettings(await api<Settings>("/api/settings"));
    });
  }
  function field(
    name: string,
    label: string,
    options: {
      type?: string;
      placeholder?: string;
      min?: number;
      max?: number;
      step?: number;
    } = {},
  ) {
    const id = `${prefix}-${name}`;
    return (
      <label htmlFor={id}>
        {label}
        <input
          id={id}
          name={name}
          type={options.type ?? "text"}
          placeholder={options.placeholder}
          min={options.min}
          max={options.max}
          step={options.step}
          autoComplete={options.type === "password" ? "new-password" : "off"}
          value={values[name] ?? ""}
          onChange={(event) =>
            setValues((current) => ({ ...current, [name]: event.target.value }))
          }
          disabled={loading || busy}
        />
      </label>
    );
  }
  function keyBadge(provider: string) {
    const configured = settingGroup(settings, provider).api_key_configured;
    return (
      <span className={`badge ${configured ? "good" : "neutral"}`}>
        {settingGroup(settings, provider).api_key_from_env
          ? "环境变量已配置"
          : configured
            ? "密钥已配置"
            : "未配置密钥"}
      </span>
    );
  }
  async function test(provider: string) {
    if (
      !(await confirm(
        `测试 ${providerNames[provider]} 连接`,
        "使用已保存的配置发起真实测试请求，可能产生调用费用。尚未保存的修改不会用于本次测试。",
        "开始测试",
      ))
    )
      return;
    await run(async () => {
      setTests((current) => ({ ...current, [provider]: { text: "测试中…" } }));
      try {
        const response = await api<{
          message?: string;
          status?: string;
          ok?: boolean;
        }>(`/api/settings/test/${provider}`, { method: "POST" });
        if (mounted.current)
          setTests((current) => ({
            ...current,
            [provider]: {
              text:
                response.message ||
                response.status ||
                (response.ok ? "连接成功" : "测试失败"),
              ok: !!response.ok,
            },
          }));
      } catch (failure) {
        if (mounted.current)
          setTests((current) => ({
            ...current,
            [provider]: { text: errorText(failure), ok: false },
          }));
      }
    });
  }
  async function testModel(model: ModelConfiguration) {
    if (
      !(await confirm(
        `测试 ${model.name || model.model} 连接`,
        "使用已保存的配置发起真实模型请求，可能计费。结果和费用将记录到调用审计。",
        "开始测试",
      ))
    )
      return;
    await run(async () => {
      setTests((current) => ({ ...current, [model.id]: { text: "测试中…" } }));
      try {
        const result = await api<{
          ok?: boolean;
          message?: string;
          status?: string;
        }>(`/api/settings/test/model/${encodeURIComponent(model.id)}`, {
          method: "POST",
        });
        if (mounted.current)
          setTests((current) => ({
            ...current,
            [model.id]: {
              text:
                result.message ||
                result.status ||
                (result.ok ? "连接成功" : "测试失败"),
              ok: !!result.ok,
            },
          }));
      } catch (failure) {
        if (mounted.current)
          setTests((current) => ({
            ...current,
            [model.id]: { text: errorText(failure), ok: false },
          }));
      }
    });
  }
  function testControl(provider: string, label: string) {
    const result = tests[provider];
    return (
      <div className="provider-test">
        <button
          type="button"
          className="button small"
          disabled={loading || busy}
          onClick={() => void test(provider)}
        >
          {label}
        </button>
        <span
          className={`test-result${result?.ok === true ? " good" : result?.ok === false ? " bad" : ""}`}
          role="status"
        >
          {result?.text}
        </span>
      </div>
    );
  }
  async function removeKey(provider: string) {
    if (
      !(await confirm(
        "移除已保存密钥？",
        `将移除本机保存的 ${provider} API key。不会撤销服务商端密钥，环境变量中的密钥也不受影响。`,
        "移除密钥",
      ))
    )
      return;
    await run(async () => {
      await api("/api/settings", {
        method: "PUT",
        body: { [provider]: { api_key: null } },
      });
      applySettings(await api<Settings>("/api/settings"));
      notify("已移除本机保存的密钥");
    });
  }
  function removeControl(provider: string) {
    return settingGroup(settings, provider).api_key_configured &&
      !settingGroup(settings, provider).api_key_from_env ? (
      <button
        type="button"
        className="button quiet danger-text small remove-key"
        disabled={busy}
        onClick={() => void removeKey(provider)}
      >
        移除已保存密钥
      </button>
    ) : null;
  }
  async function clear(kind: "cache" | "manual") {
    const manual = kind === "manual";
    if (
      !(await confirm(
        manual ? "删除人工导入记录？" : "清除查询缓存？",
        manual
          ? "删除所有可供后续检查复用的人工题录。历史任务中的证据快照仍保留，需单独删除对应任务才会移除。此操作不可撤销。"
          : "仅删除可复用的查询缓存。任务历史、上传文件及历史证据均保留。下次检查可能重新调用在线来源并产生费用。",
        manual ? "删除人工题录" : "清除缓存",
      ))
    )
      return;
    await run(async () => {
      const result = await api<{ deleted?: number }>(
        manual ? "/api/manual-records" : "/api/cache/clear",
        { method: manual ? "DELETE" : "POST" },
      );
      notify(
        `已${manual ? "删除" : "清除"} ${result.deleted ?? ""} 条${manual ? "人工记录" : "缓存"}，历史证据保留。`,
      );
    });
  }
  return (
    <Modal
      id="settings-dialog"
      title="设置"
      className="settings-dialog"
      onClose={onClose}
      busy={busy}
    >
      <form onSubmit={save}>
        <div className="settings-content">
          <p className="settings-notice">
            密钥仅保存在本机后端。留空保留已有密钥。
          </p>
          {loading && <p role="status">加载设置中…</p>}
          {hasProviders ? (
            <ModelSettings
              value={models}
              saved={savedModels}
              catalog={catalog}
              disabled={loading || busy}
              onChange={setModels}
              onTest={(model) => void testModel(model)}
              testResults={tests}
              confirm={confirm}
            />
          ) : (
            <section className="provider-settings">
              <div className="section-heading">
                <h3>大模型</h3>
                {keyBadge("llm")}
              </div>
              {field("llm.base_url", "Base URL", {
                type: "url",
                placeholder: "https://api.example.com/v1",
              })}
              <div className="form-columns">
                {field("llm.model", "模型", { placeholder: "模型名称" })}
                {field("llm.api_key", "API key", {
                  type: "password",
                  placeholder: "输入新密钥",
                })}
              </div>
              {testControl("llm", "测试大模型连接")}
              {removeControl("llm")}
            </section>
          )}
          <section className="provider-settings">
            <div className="section-heading">
              <h3>DeepSeek Search / Fetch</h3>
              {keyBadge("deepseek")}
            </div>
            {field("deepseek.api_key", "API key", {
              type: "password",
              placeholder: "输入新密钥",
            })}
            {testControl("deepseek", "测试搜索 / Fetch 能力")}
            {removeControl("deepseek")}
          </section>
          <section className="provider-settings">
            <div className="section-heading">
              <h3>Semantic Scholar</h3>
              {keyBadge("semantic_scholar")}
            </div>
            <div className="form-columns">
              {field("semantic_scholar.api_key", "API key", {
                type: "password",
                placeholder: "输入新密钥",
              })}
              {field(
                "semantic_scholar.rate_per_second",
                "获批速率（次 / 秒）",
                { type: "number", min: 0.05, max: 10, step: 0.05 },
              )}
            </div>
            {testControl("semantic_scholar", "测试 Semantic Scholar")}
            {removeControl("semantic_scholar")}
          </section>
          <details className="advanced-settings">
            <summary>公开来源、调用预算与缓存</summary>
            <div className="public-tests">
              {testControl("crossref", "测试 Crossref")}
              {testControl("arxiv", "测试 arXiv")}
            </div>
            <h3>调用预算</h3>
            <div className="form-columns">
              {field("budget.per_entry", "单条上限", {
                type: "number",
                min: 1,
                max: 100,
                step: 1,
              })}
              {field("budget.per_task", "单任务上限", {
                type: "number",
                min: 1,
                max: 5000,
                step: 1,
              })}
            </div>
            <h3>建议复核周期（天）</h3>
            <div className="form-columns three">
              {field("cache.metadata_days", "元数据", {
                type: "number",
                min: 1,
                max: 365,
              })}
              {field("cache.search_days", "检索结果", {
                type: "number",
                min: 1,
                max: 365,
              })}
              {field("cache.negative_days", "无结果", {
                type: "number",
                min: 1,
                max: 365,
              })}
            </div>
            <button
              type="button"
              className="button danger-text small"
              onClick={() => void clear("cache")}
              disabled={loading || busy}
            >
              清除查询缓存
            </button>
            <p className="subtle cache-note">
              不删除任务历史、上传文件或历史证据。
            </p>
            <button
              type="button"
              className="button danger-text small"
              onClick={() => void clear("manual")}
              disabled={loading || busy}
            >
              删除人工导入记录
            </button>
            <p className="subtle cache-note">
              停止在后续检查中复用；历史任务中的证据快照保留。
            </p>
          </details>
          <p className="settings-notice">
            连接测试使用已保存配置，会发起真实请求，可能计费。
          </p>
          <div className="inline-error" role="alert">
            {error}
          </div>
        </div>
        <div className="dialog-footer">
          <button
            type="button"
            className="button quiet"
            onClick={onClose}
            disabled={busy}
          >
            关闭
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={loading || busy || !settings}
          >
            {busy ? "处理中…" : "保存设置"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
