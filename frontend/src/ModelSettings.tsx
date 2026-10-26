import { useId } from "react";
import { Badge, Button } from "./ui";

export interface ModelProvider {
  id: string;
  name: string;
  client_type: string;
  base_url: string;
  api_key?: string | null;
  api_key_configured?: boolean;
  api_key_from_env?: boolean;
}
export interface ModelConfiguration {
  id: string;
  name: string;
  provider_id: string;
  model: string;
  temperature: number;
  timeout: number;
  max_output_tokens: number;
  max_retries: number;
}
export interface ModelConfigurationSet {
  llm_providers: ModelProvider[];
  llm_models: ModelConfiguration[];
  default_model_id: string;
}
export interface CatalogModel {
  model: string;
  base_url: string;
  client: string;
  pricing?: Record<string, number | string | null>;
  supported_client_types?: string[];
}
export interface ModelCatalog {
  models: CatalogModel[];
  currency: string;
}
export const emptyModels: ModelConfigurationSet = {
  llm_providers: [],
  llm_models: [],
  default_model_id: "",
};
const protocolLabels: Record<string, string> = {
  "openai-chat": "OpenAI Chat Completions",
  "openai-responses": "OpenAI Responses",
  "ant-messages": "Anthropic Messages",
};
const priceLabels: Record<string, string> = {
  cached_tokens: "缓存输入",
  prompt_tokens: "输入",
  thoughts_tokens: "思考",
  response_tokens: "输出",
};
function identifier(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}
export function modelPayload(value: ModelConfigurationSet) {
  return {
    ...value,
    llm_providers: value.llm_providers.map(
      ({ id, name, client_type, base_url, api_key }) => ({
        id,
        name,
        client_type,
        base_url,
        api_key: api_key === null ? null : api_key || "",
      }),
    ),
  };
}
export function modelLabel(
  model: ModelConfiguration,
  providers: ModelProvider[],
) {
  return `${providers.find((provider) => provider.id === model.provider_id)?.name || model.provider_id} / ${model.name || model.model}`;
}
function priceFor(
  model: ModelConfiguration,
  provider: ModelProvider,
  catalog: ModelCatalog | null,
) {
  const matches =
    catalog?.models.filter(
      (item) =>
        item.model === model.model &&
        (item.supported_client_types || [item.client]).includes(
          provider.client_type,
        ) &&
        item.base_url.replace(/\/+$/, "") ===
          provider.base_url.replace(/\/+$/, ""),
    ) || [];
  const prices = matches.map((item) => item.pricing);
  const signature = (price: CatalogModel["pricing"]) =>
    JSON.stringify(
      Object.entries(price || {}).sort(([a], [b]) => a.localeCompare(b)),
    );
  return prices.length &&
    prices.every((price) => signature(price) === signature(prices[0]))
    ? prices[0]
    : undefined;
}

export function ModelSettings({
  value,
  saved,
  catalog,
  disabled,
  onChange,
  onTest,
  testResults,
  confirm,
}: {
  value: ModelConfigurationSet;
  saved: ModelConfigurationSet;
  catalog: ModelCatalog | null;
  disabled: boolean;
  onChange: (next: ModelConfigurationSet) => void;
  onTest: (model: ModelConfiguration) => void;
  testResults: Record<string, { text: string; ok?: boolean }>;
  confirm: (
    title: string,
    description: string,
    label?: string,
  ) => Promise<boolean>;
}) {
  const prefix = useId();
  function updateProvider(id: string, change: Partial<ModelProvider>) {
    onChange({
      ...value,
      llm_providers: value.llm_providers.map((item) =>
        item.id === id ? { ...item, ...change } : item,
      ),
    });
  }
  function updateModel(id: string, change: Partial<ModelConfiguration>) {
    onChange({
      ...value,
      llm_models: value.llm_models.map((item) =>
        item.id === id ? { ...item, ...change } : item,
      ),
    });
  }
  async function removeProvider(provider: ModelProvider) {
    if (
      !(await confirm(
        "移除 provider？",
        `保存后将移除 ${provider.name || "此 provider"} 及其模型配置。既有任务与调用记录保留，不撤销服务商端密钥。`,
        "移除",
      ))
    )
      return;
    const models = value.llm_models.filter(
      (item) => item.provider_id !== provider.id,
    );
    onChange({
      llm_providers: value.llm_providers.filter(
        (item) => item.id !== provider.id,
      ),
      llm_models: models,
      default_model_id: models.some(
        (item) => item.id === value.default_model_id,
      )
        ? value.default_model_id
        : "",
    });
  }
  async function removeModel(model: ModelConfiguration) {
    if (
      !(await confirm(
        "移除模型配置？",
        `保存后移除 ${model.name || model.model || "此模型"}，既有调用记录保留。`,
        "移除",
      ))
    )
      return;
    onChange({
      ...value,
      llm_models: value.llm_models.filter((item) => item.id !== model.id),
      default_model_id:
        value.default_model_id === model.id ? "" : value.default_model_id,
    });
  }
  function canTest(model: ModelConfiguration, provider: ModelProvider) {
    const previous = saved.llm_models.find((item) => item.id === model.id);
    const priorProvider = saved.llm_providers.find(
      (item) => item.id === provider.id,
    );
    return (
      !!previous &&
      !!priorProvider &&
      !!model.model.trim() &&
      JSON.stringify(previous) === JSON.stringify(model) &&
      !provider.api_key &&
      provider.api_key !== null &&
      ["name", "base_url", "client_type"].every(
        (key) =>
          provider[key as keyof ModelProvider] ===
          priorProvider[key as keyof ModelProvider],
      )
    );
  }
  return (
    <section className="model-settings" aria-label="模型 providers">
      <div className="section-heading">
        <h3>模型 providers</h3>
        <Button
          className="small"
          icon="plus"
          disabled={disabled}
          onClick={() =>
            onChange({
              ...value,
              llm_providers: [
                ...value.llm_providers,
                {
                  id: identifier("provider"),
                  name: "",
                  client_type: "openai-chat",
                  base_url: "",
                  api_key: "",
                },
              ],
            })
          }
        >
          添加 provider
        </Button>
      </div>
      {!value.llm_providers.length && (
        <p className="notice">尚未配置模型 provider。</p>
      )}
      {value.llm_providers.map((provider, providerIndex) => {
        const models = value.llm_models.filter(
          (model) => model.provider_id === provider.id,
        );
        const protocolOptions = {
          ...protocolLabels,
          ...(!protocolLabels[provider.client_type]
            ? { [provider.client_type]: provider.client_type }
            : {}),
        };
        return (
          <fieldset
            className="provider-card"
            key={provider.id}
            disabled={disabled}
          >
            <legend>{provider.name || `Provider ${providerIndex + 1}`}</legend>
            <div className="provider-card-heading">
              <Badge tone={provider.api_key_configured ? "good" : "neutral"}>
                {provider.api_key === null
                  ? "保存后移除密钥"
                  : provider.api_key_from_env
                    ? "环境变量密钥"
                    : provider.api_key_configured
                      ? "密钥已配置"
                      : "未配置密钥"}
              </Badge>
              <Button
                className="small quiet danger-text"
                onClick={() => void removeProvider(provider)}
              >
                移除 provider
              </Button>
            </div>
            <div className="form-columns">
              <label>
                名称
                <input
                  value={provider.name}
                  onChange={(event) =>
                    updateProvider(provider.id, { name: event.target.value })
                  }
                  required
                  placeholder="例如 OpenAI 官方"
                />
              </label>
              <label>
                协议
                <select
                  value={provider.client_type}
                  onChange={(event) =>
                    updateProvider(provider.id, {
                      client_type: event.target.value,
                    })
                  }
                >
                  {Object.entries(protocolOptions).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              Base URL
              <input
                type="url"
                required
                value={provider.base_url}
                placeholder="https://api.example.com/v1"
                onChange={(event) =>
                  updateProvider(provider.id, { base_url: event.target.value })
                }
                autoComplete="off"
              />
            </label>
            <label>
              API key
              <input
                type="password"
                value={provider.api_key || ""}
                placeholder="留空保留已有密钥"
                onChange={(event) =>
                  updateProvider(provider.id, { api_key: event.target.value })
                }
                autoComplete="new-password"
              />
            </label>
            {provider.api_key_configured && !provider.api_key_from_env && (
              <Button
                className="small quiet danger-text"
                onClick={async () => {
                  if (
                    await confirm(
                      "移除已保存密钥？",
                      "保存设置后移除本机密钥，不撤销服务商端密钥。",
                      "移除密钥",
                    )
                  )
                    updateProvider(provider.id, { api_key: null });
                }}
              >
                移除已保存密钥
              </Button>
            )}
            {models.map((model) => {
              const result = testResults[model.id];
              const price = priceFor(model, provider, catalog);
              const catalogId = `${prefix}-${model.id}-catalog`;
              return (
                <div className="model-card" key={model.id}>
                  <div className="section-heading">
                    <h4>{model.name || model.model || "新模型"}</h4>
                    <Button
                      className="small quiet danger-text"
                      onClick={() => void removeModel(model)}
                    >
                      移除模型
                    </Button>
                  </div>
                  <div className="form-columns">
                    <label>
                      显示名称
                      <input
                        value={model.name}
                        onChange={(event) =>
                          updateModel(model.id, { name: event.target.value })
                        }
                        placeholder="可选"
                      />
                    </label>
                    <label>
                      模型 ID
                      <input
                        list={catalogId}
                        required={model.id !== "legacy"}
                        value={model.model}
                        onChange={(event) =>
                          updateModel(model.id, { model: event.target.value })
                        }
                        autoComplete="off"
                      />
                      <datalist id={catalogId}>
                        {catalog?.models
                          .filter(
                            (item) =>
                              (
                                item.supported_client_types || [item.client]
                              ).includes(provider.client_type) &&
                              item.base_url.replace(/\/+$/, "") ===
                                provider.base_url.replace(/\/+$/, ""),
                          )
                          .map((item) => (
                            <option key={item.model} value={item.model} />
                          ))}
                      </datalist>
                    </label>
                  </div>
                  <details className="model-parameters">
                    <summary>调用参数</summary>
                    <div className="form-columns">
                      <label>
                        Temperature
                        <input
                          type="number"
                          min="0"
                          max="2"
                          step="0.1"
                          value={model.temperature}
                          onChange={(event) =>
                            updateModel(model.id, {
                              temperature: Number(event.target.value),
                            })
                          }
                          required
                        />
                      </label>
                      <label>
                        输出 token 上限
                        <input
                          type="number"
                          min="128"
                          max="8000"
                          step="1"
                          value={model.max_output_tokens}
                          onChange={(event) =>
                            updateModel(model.id, {
                              max_output_tokens: Number(event.target.value),
                            })
                          }
                          required
                        />
                      </label>
                      <label>
                        超时（秒）
                        <input
                          type="number"
                          min="1"
                          max="120"
                          step="1"
                          value={model.timeout}
                          onChange={(event) =>
                            updateModel(model.id, {
                              timeout: Number(event.target.value),
                            })
                          }
                          required
                        />
                      </label>
                      <label>
                        失败重试次数
                        <input
                          type="number"
                          min="0"
                          max="5"
                          step="1"
                          value={model.max_retries}
                          onChange={(event) =>
                            updateModel(model.id, {
                              max_retries: Number(event.target.value),
                            })
                          }
                          required
                        />
                      </label>
                    </div>
                  </details>
                  <p className="model-price">
                    {price
                      ? Object.entries(priceLabels)
                          .map(
                            ([key, label]) =>
                              `${label} ${typeof price[key] === "number" ? price[key] : "未知"}`,
                          )
                          .join(" · ") +
                        ` ${catalog?.currency || "USD"} / 百万 token`
                      : catalog
                        ? "MMSP 未匹配到价格，费用将标记为未知。"
                        : "价格目录尚不可用，费用暂标记为未知。"}
                  </p>
                  <div className="provider-test">
                    <Button
                      className="small"
                      disabled={disabled || !canTest(model, provider)}
                      title={
                        !canTest(model, provider)
                          ? "先保存配置再测试"
                          : undefined
                      }
                      onClick={() => onTest(model)}
                    >
                      测试模型连接
                    </Button>
                    <span
                      className={`test-result${result?.ok === true ? " good" : result?.ok === false ? " bad" : ""}`}
                      role="status"
                    >
                      {result?.text}
                    </span>
                  </div>
                </div>
              );
            })}
            <Button
              className="small"
              icon="plus"
              onClick={() => {
                const model: ModelConfiguration = {
                  id: identifier("model"),
                  provider_id: provider.id,
                  name: "",
                  model: "",
                  temperature: 0,
                  timeout: 45,
                  max_output_tokens: 1500,
                  max_retries: 2,
                };
                onChange({
                  ...value,
                  llm_models: [...value.llm_models, model],
                  default_model_id: value.default_model_id || model.id,
                });
              }}
            >
              添加模型
            </Button>
          </fieldset>
        );
      })}
      {!!value.llm_models.length && (
        <label>
          默认模型
          <select
            value={value.default_model_id}
            onChange={(event) =>
              onChange({ ...value, default_model_id: event.target.value })
            }
            disabled={disabled}
          >
            <option value="">不使用大模型</option>
            {value.llm_models.map((model) => (
              <option key={model.id} value={model.id}>
                {modelLabel(model, value.llm_providers)}
              </option>
            ))}
          </select>
        </label>
      )}
    </section>
  );
}
