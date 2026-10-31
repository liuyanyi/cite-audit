import { createContext, useContext, useEffect, useRef, useState } from 'react';
import type { ButtonHTMLAttributes, ComponentProps, ReactNode } from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import * as SelectPrimitive from '@radix-ui/react-select';
import * as SwitchPrimitive from '@radix-ui/react-switch';
import * as CheckboxPrimitive from '@radix-ui/react-checkbox';
import * as DropdownPrimitive from '@radix-ui/react-dropdown-menu';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { AlertCircle, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Copy, FileSearch, LoaderCircle, MoreHorizontal, Search, X } from 'lucide-react';

export function Button({ children, variant = 'secondary', className = '', type = 'button', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' }) {
  return <button type={type} data-slot="button" className={`button ${variant} ${className}`} {...props}>{children}</button>;
}
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' }) {
  return <span data-slot="badge" className={`badge ${tone}`}>{children}</span>;
}
export function Switch(props: ComponentProps<typeof SwitchPrimitive.Root>) {
  return <SwitchPrimitive.Root {...props} className={`switch ${props.className ?? ''}`}><SwitchPrimitive.Thumb className="switch-thumb" /></SwitchPrimitive.Root>;
}
export function Checkbox(props: ComponentProps<typeof CheckboxPrimitive.Root>) {
  return <CheckboxPrimitive.Root {...props} className={`checkbox ${props.className ?? ''}`}><CheckboxPrimitive.Indicator><Check size={12} strokeWidth={3} /></CheckboxPrimitive.Indicator></CheckboxPrimitive.Root>;
}
export function Select({ value, onValueChange, options, className = '', id, disabled, ...props }: {
  value: string; onValueChange: (value: string) => void; options: { value: string; label: ReactNode; disabled?: boolean }[];
  className?: string; id?: string; disabled?: boolean; 'aria-label'?: string;
}) {
  const empty = '__cite_audit_empty_selection__';
  return <SelectPrimitive.Root value={value || empty} onValueChange={next => onValueChange(next === empty ? '' : next)} disabled={disabled}>
    <SelectPrimitive.Trigger id={id} aria-label={props['aria-label']} className={`select-trigger ${className}`}>
      <SelectPrimitive.Value /> <SelectPrimitive.Icon><ChevronDown size={14} /></SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
    <SelectPrimitive.Portal><SelectPrimitive.Content className="select-content" position="popper" sideOffset={5} collisionPadding={12}>
      <SelectPrimitive.ScrollUpButton className="select-scroll"><ChevronUp size={14} /></SelectPrimitive.ScrollUpButton>
      <SelectPrimitive.Viewport className="select-viewport">{options.map(option => <SelectPrimitive.Item className="select-item" key={option.value} value={option.value || empty} disabled={option.disabled}>
        <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText><SelectPrimitive.ItemIndicator className="select-indicator"><Check size={14} /></SelectPrimitive.ItemIndicator>
      </SelectPrimitive.Item>)}</SelectPrimitive.Viewport>
      <SelectPrimitive.ScrollDownButton className="select-scroll"><ChevronDown size={14} /></SelectPrimitive.ScrollDownButton>
    </SelectPrimitive.Content></SelectPrimitive.Portal>
  </SelectPrimitive.Root>;
}
export function ActionMenu({ label, items }: { label: string; items: { label: string; onSelect: () => void; icon?: ReactNode; destructive?: boolean; disabled?: boolean }[] }) {
  return <DropdownPrimitive.Root><DropdownPrimitive.Trigger asChild><Button className="icon-button compact" variant="ghost" aria-label={label}><MoreHorizontal size={18} /></Button></DropdownPrimitive.Trigger>
    <DropdownPrimitive.Portal><DropdownPrimitive.Content className="dropdown-content" align="end" sideOffset={5} collisionPadding={12}>{items.map((item, i) => <DropdownPrimitive.Item key={i} disabled={item.disabled} onSelect={item.onSelect} className={`dropdown-item ${item.destructive ? 'destructive' : ''}`}>{item.icon}{item.label}</DropdownPrimitive.Item>)}</DropdownPrimitive.Content></DropdownPrimitive.Portal>
  </DropdownPrimitive.Root>;
}
export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  return <TooltipPrimitive.Provider delayDuration={350}><TooltipPrimitive.Root><TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger><TooltipPrimitive.Portal><TooltipPrimitive.Content className="tooltip" sideOffset={7}>{label}</TooltipPrimitive.Content></TooltipPrimitive.Portal></TooltipPrimitive.Root></TooltipPrimitive.Provider>;
}
export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-symbol"><FileSearch size={23} /></div><h3>{title}</h3>{children && <p>{children}</p>}{action}</div>;
}
export function Loading({ label = '正在读取…' }: { label?: string }) {
  return <div className="loading" role="status"><LoaderCircle className="spin" size={18} />{label}</div>;
}
export function InlineError({ error, retry }: { error: string; retry?: () => void }) {
  return <div className="notice danger" role="alert"><AlertCircle size={17} /><div><strong>未能完成请求</strong><p>{error}</p></div>{retry && <Button onClick={retry}>重试</Button>}</div>;
}
export function SearchInput({ value, onChange, placeholder = '搜索…', label = '搜索' }: { value: string; onChange: (value: string) => void; placeholder?: string; label?: string }) {
  return <label className="search"><Search size={16} /><input aria-label={label} value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)} />{value && <button aria-label="清除搜索" onClick={() => onChange('')} type="button"><X size={14} /></button>}</label>;
}
export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return <div className="page-heading"><div><h1>{title}</h1>{description && <div className="page-description">{description}</div>}</div>{actions && <div className="actions">{actions}</div>}</div>;
}
export function Section({ title, action, children, className = '' }: { title: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="section-heading"><h2>{title}</h2>{action}</div>{children}</section>;
}
export function Tabs({ tabs, value, onChange, label }: { tabs: { id: string; label: ReactNode }[]; value: string; onChange: (id: string) => void; label: string }) {
  return <TabsPrimitive.Root value={value} onValueChange={onChange}><TabsPrimitive.List className="tabs" aria-label={label}>{tabs.map(tab => <TabsPrimitive.Trigger key={tab.id} value={tab.id}>{tab.label}</TabsPrimitive.Trigger>)}</TabsPrimitive.List></TabsPrimitive.Root>;
}
export function Pagination({ page, total, size, onPage, onSize }: { page: number; total: number; size: number; onPage: (page: number) => void; onSize?: (size: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return <div className="pagination"><span>共 {total} 条</span><div className="pagination-options">{onSize && <><span>每页</span><Select aria-label="每页条数" value={String(size)} onValueChange={value => onSize(Number(value))} options={[10, 20, 50].map(v => ({ value: String(v), label: `${v} 条` }))} /></>}<span className="page-number">第 {page} / {pages} 页</span><div className="pagination-controls"><Button aria-label="上一页" disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft size={15} /></Button><Button aria-label="下一页" disabled={page >= pages} onClick={() => onPage(page + 1)}><ChevronRight size={15} /></Button></div></div></div>;
}
export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const previousFocus = useRef(document.activeElement as HTMLElement | null);
  return <DialogPrimitive.Root open onOpenChange={open => { if (!open) onClose(); }}><DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="modal-overlay" />
    <DialogPrimitive.Content className={`modal ${wide ? 'wide' : ''}`} aria-describedby={undefined} onCloseAutoFocus={event => { event.preventDefault(); previousFocus.current?.focus(); }}>
      <div className="modal-heading"><DialogPrimitive.Title>{title}</DialogPrimitive.Title><DialogPrimitive.Close asChild><Button className="icon-button" variant="ghost" aria-label="关闭对话框"><X size={17} /></Button></DialogPrimitive.Close></div>{children}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal></DialogPrimitive.Root>;
}

type Toast = { text: string; error?: boolean };
const ToastContext = createContext<(text: string, error?: boolean) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<Toast | null>(null);
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(null), 5000); return () => clearTimeout(timer); }, [toast]);
  return <ToastContext.Provider value={(text, error) => setToast({ text, error })}>{children}{toast && <div className={`toast ${toast.error ? 'error' : ''}`} role="status">{toast.error ? <AlertCircle size={17} /> : <Check size={17} />}{toast.text}<button aria-label="关闭通知" onClick={() => setToast(null)}><X size={15} /></button></div>}</ToastContext.Provider>;
}
export const useToast = () => useContext(ToastContext);
export function CopyButton({ text, label = '复制', small = false }: { text: string; label?: string; small?: boolean }) {
  const toast = useToast();
  const copy = async () => { try { await navigator.clipboard.writeText(text); toast('已复制到剪贴板'); } catch { toast('无法访问剪贴板，请手动选中复制。', true); } };
  return <Tooltip label={label}><Button className={small ? 'compact' : ''} onClick={copy} aria-label={label}><Copy size={14} />{label}</Button></Tooltip>;
}
export function BibCode({ text, label }: { text: string; label: string }) {
  return <div className="code-block"><div className="code-heading"><span>{label}</span><CopyButton text={text} label="复制 BibTeX" small /></div><pre>{text || '暂无可安全生成的建议'}</pre></div>;
}
