import { Navigate, Route, Routes } from 'react-router-dom';
import Shell from './components/Shell';
import { ToastProvider } from './components/ui';
import TaskList from './pages/TaskList';
import TaskDetail from './pages/TaskDetail';
import EntryDetail from './pages/EntryDetail';
import Knowledge from './pages/Knowledge';
import Settings from './pages/Settings';
export default function App() { return <ToastProvider><Routes><Route element={<Shell />}><Route index element={<Navigate to="/tasks" replace />} /><Route path="tasks" element={<TaskList />} /><Route path="tasks/:taskId" element={<TaskDetail />} /><Route path="tasks/:taskId/entries/:entryId" element={<EntryDetail />} /><Route path="knowledge" element={<Knowledge />} /><Route path="knowledge/:articleId" element={<Knowledge />} /><Route path="settings" element={<Navigate to="/settings/sources" replace />} /><Route path="settings/:section" element={<Settings />} /><Route path="*" element={<div className="empty-state"><h1>页面不存在</h1><a href="/tasks">返回核查任务</a></div>} /></Route></Routes></ToastProvider>; }
