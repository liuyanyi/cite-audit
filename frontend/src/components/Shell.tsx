import { useEffect, useState } from 'react';
import * as Collapsible from '@radix-ui/react-collapsible';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { BookOpen, ChevronRight, Database, FileCheck2, Github, Menu, Moon, Settings2, Sun, X } from 'lucide-react';
import { settingsSections } from '../settingsNavigation';
import { Button, Modal } from './ui';
import HelpContent from './HelpContent';

export default function Shell() {
  const [dark, setDark] = useState(() => localStorage.getItem('cite-audit-theme') === 'dark');
  const [open, setOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const location = useLocation();
  const isSettings = location.pathname.startsWith('/settings');
  const [settingsOpen, setSettingsOpen] = useState(isSettings);
  const currentSection = settingsSections.find(item => location.pathname === `/settings/${item.id}`);
  useEffect(() => { if (location.pathname.startsWith('/settings')) setSettingsOpen(true); }, [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [open]);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const theme = () => { localStorage.setItem('cite-audit-theme', dark ? 'light' : 'dark'); setDark(!dark); };
  const label = location.pathname.startsWith('/knowledge') ? '文献知识库' : location.pathname.startsWith('/settings') ? '设置' : '核查任务';
  return <div className="app-shell"><a className="skip-link" href="#main-content">跳至主要内容</a>{open && <button className="sidebar-backdrop" aria-label="收起导航" onClick={() => setOpen(false)} />}<aside id="main-sidebar" className={`sidebar ${open ? 'mobile-open' : ''}`}><NavLink className="brand" aria-label="Cite-Audit" to="/tasks" onClick={() => setOpen(false)}><span className="brand-icon" aria-hidden="true" /></NavLink><nav aria-label="主导航"><NavLink to="/tasks" onClick={() => setOpen(false)}><FileCheck2 size={18} />核查任务</NavLink><NavLink to="/knowledge" onClick={() => setOpen(false)}><Database size={18} />文献知识库</NavLink><Collapsible.Root className="nav-group" open={settingsOpen} onOpenChange={setSettingsOpen}><Collapsible.Trigger asChild><button type="button" className={`nav-group-trigger ${isSettings ? 'group-active' : ''}`} ><Settings2 size={18} /><span>设置</span><ChevronRight className="nav-chevron" size={15} /></button></Collapsible.Trigger><Collapsible.Content asChild><ul id="settings-submenu" className="nav-submenu">{settingsSections.map(item => <li key={item.id}><NavLink to={`/settings/${item.id}`} end onClick={() => setOpen(false)}>{item.label}</NavLink></li>)}</ul></Collapsible.Content></Collapsible.Root></nav><div className="sidebar-bottom"><button className="help-link" onClick={() => setHelp(true)}><BookOpen size={17} />使用说明<ChevronRight size={14} /></button><div className="sidebar-footer"><a className="github-link" href="https://github.com/liuyanyi/cite-audit" target="_blank" rel="noopener noreferrer" aria-label="在 GitHub 查看项目"><Github size={19} aria-hidden="true" />GitHub</a><span className="version">v0.1</span></div></div></aside><div className="main-shell"><header className="topbar"><div className="topbar-left"><Button className="menu-toggle" variant="ghost" aria-expanded={open} aria-controls="main-sidebar" aria-label={open ? '收起导航' : '打开导航'} onClick={() => setOpen(!open)}>{open ? <X size={18} /> : <Menu size={18} />}</Button><span className="topbar-app">工作空间</span><ChevronRight size={13} /><span>{label}</span>{currentSection && <><ChevronRight size={13} /><span>{currentSection.label}</span></>}</div><div className="topbar-right"><Button variant="ghost" aria-label={dark ? '切换浅色模式' : '切换深色模式'} onClick={theme}>{dark ? <Sun size={17} /> : <Moon size={17} />}</Button></div></header><main id="main-content"><Outlet /></main></div>{help && <Modal title="使用说明" onClose={() => setHelp(false)}><HelpContent /><div className="modal-actions"><Button variant="primary" onClick={() => setHelp(false)}>明白了</Button></div></Modal>}</div>;
}
