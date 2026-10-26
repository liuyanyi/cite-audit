import type { ButtonHTMLAttributes, ReactNode } from "react";

const paths: Record<string, ReactNode> = {
  upload: <path d="M12 16V4m-5 5 5-5 5 5M4 15v5h16v-5" />,
  plus: <path d="M12 5v14M5 12h14" />,
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 5 5" />
    </>
  ),
  settings: (
    <>
      <path d="m9 4 1-2h4l1 2 3 2 2 .5 2 3-1.5 2v3L22 17l-2 3-2-.5-3 2-1 1h-4l-1-2-3-1-2 .5-2-3 1.5-2v-3L2 9l2-3 2 .5z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
  link: <path d="M14 4h6v6m0-6L9 15M10 5H4v15h15v-6" />,
  copy: (
    <>
      <rect x="8" y="8" width="12" height="13" rx="2" />
      <path d="M15 8V3H3v13h5" />
    </>
  ),
  refresh: (
    <path d="M20 7v5h-5M4 17v-5h5M5.3 7a8 8 0 0 1 13.6-1L20 9M4 15l1.1 3A8 8 0 0 0 18.7 17" />
  ),
  check: <path d="m5 12 4 4L19 6" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  document: <path d="M14 3H5v18h14V8zm0 0v5h5M8 12h8M8 16h6" />,
  download: <path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5" />,
  alert: <path d="m12 3 10 18H2zM12 9v5m0 3v1" />,
};
export function Icon({ name }: { name: string }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}
export function Button({
  children,
  className = "",
  icon,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon?: string }) {
  return (
    <button {...props} type={type} className={`button ${className}`}>
      {icon && <Icon name={icon} />}
      {children}
    </button>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function safeURL(value: unknown) {
  try {
    const url = new URL(String(value));
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export function SafeLink({
  children,
  url,
  className = "",
}: {
  children: ReactNode;
  url?: string;
  className?: string;
}) {
  const href = safeURL(url);
  return href ? (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={className}
    >
      {children}
      <Icon name="link" />
    </a>
  ) : (
    <span className="subtle">{children}</span>
  );
}
export function str(value: unknown): string {
  return value == null || value === ""
    ? ""
    : typeof value === "object"
      ? JSON.stringify(value, null, 2)
      : String(value);
}
export function issueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const item = value as Record<string, unknown>;
    return str(item.message || item.reason || item.code || value);
  }
  return str(value);
}
export function formatDate(value?: string | number, withTime = false): string {
  if (!value) return "时间未知";
  const date = new Date(
    typeof value === "number" && value < 1e12 ? value * 1000 : value,
  );
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString(
        "zh-CN",
        withTime
          ? {
              year: "numeric",
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            }
          : { month: "2-digit", day: "2-digit" },
      );
}
export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("aria-label", "复制内容");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  try {
    if (!document.execCommand("copy"))
      throw new Error("复制失败，请选中文本后手动复制。");
  } finally {
    area.remove();
  }
}
