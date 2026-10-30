import { createContext, useContext, useEffect, useRef, useState } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { AlertCircle, Check, ChevronLeft, ChevronRight, Copy, LoaderCircle, Search, X } from 'lucide-react';

export function Button({ children, variant = 'secondary', className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' }) {
  return <button className={`button ${variant} ${className}`} {...props}>{children}</button>;
}
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' }) {
  return <span className={`badge ${tone}`}><span className="status-dot" />{children}</span>;
}
export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-symbol">⌕</div><h3>{title}</h3>{children && <p>{children}</p>}{action}</div>;
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
  return <div className="tabs" role="tablist" aria-label={label}>{tabs.map(tab => <button key={tab.id} role="tab" type="button" aria-selected={value === tab.id} className={value === tab.id ? 'active' : ''} onClick={() => onChange(tab.id)}>{tab.label}</button>)}</div>;
}
export function Pagination({ page, total, size, onPage, onSize }: { page: number; total: number; size: number; onPage: (page: number) => void; onSize?: (size: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return <div className="pagination"><span>共 {total} 条</span>{onSize && <label>每页 <select aria-label="每页条数" value={size} onChange={e => onSize(Number(e.target.value))}>{[10, 20, 50].map(v => <option key={v} value={v}>{v} 条</option>)}</select></label>}<div className="pagination-controls"><Button aria-label="上一页" disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft size={15} /></Button><span className="page-number">{page} / {pages}</span><Button aria-label="下一页" disabled={page >= pages} onClick={() => onPage(page + 1)}><ChevronRight size={15} /></Button></div></div>;
}
export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]') ?? []);
    focusable()[0]?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeRef.current();
      if (e.key === 'Tab') {
        const elements = focusable(); const first = elements[0]; const last = elements[elements.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', key);
    const old = document.body.style.overflow; document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', key); document.body.style.overflow = old; previous?.focus(); };
  }, []);
  return <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}><div ref={dialogRef} className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby="dialog-title"><div className="modal-heading"><h2 id="dialog-title">{title}</h2><Button variant="ghost" aria-label="关闭对话框" onClick={onClose}><X size={18} /></Button></div>{children}</div></div>;
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
  return <Button className={small ? 'compact' : ''} onClick={copy} aria-label={label}><Copy size={14} />{label}</Button>;
}
export function BibCode({ text, label }: { text: string; label: string }) {
  return <div className="code-block"><div className="code-heading"><span>{label}</span><CopyButton text={text} label="复制 BibTeX" small /></div><pre>{text || '暂无可安全生成的建议'}</pre></div>;
}
