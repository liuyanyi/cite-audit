import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { UploadDialog } from '../components/UploadDialog';
import { settingsFixture as settings } from './fixtures';

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const bib = (name = 'paper.bib') => new File(['@misc{x, title={A paper}}'], name, { type: 'application/x-bibtex' });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(async () => response(settings)); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const mount = () => render(<MemoryRouter><UploadDialog onClose={vi.fn()} /></MemoryRouter>);
const zone = () => document.querySelector('.upload-zone')!;
const drop = (files: File[]) => fireEvent.drop(zone(), { dataTransfer: { files } });

it('uses an accessible button for a visually hidden file input', async () => {
  mount(); const input = screen.getByLabelText('完整 BibTeX 文件');
  expect(input).toHaveAttribute('tabindex', '-1');
  const click = vi.spyOn(input, 'click');
  await userEvent.click(screen.getByRole('button', { name: '选择文件' })); expect(click).toHaveBeenCalledOnce();
});
it('accepts a dropped Bib and shows a compact selected file card', async () => {
  mount(); drop([bib()]);
  expect(screen.getByText('paper.bib')).toBeVisible();
  await waitFor(() => expect(screen.getByRole('button', { name: '创建演示任务' })).toBeEnabled());
  expect(screen.getByLabelText('任务名称')).toHaveValue('paper'); expect(screen.getByRole('button', { name: '重新选择' })).toBeVisible();
});
it('can remove and select the same file again', async () => {
  mount(); const file = bib(); const input = screen.getByLabelText('完整 BibTeX 文件');
  await userEvent.upload(input, file); await waitFor(() => expect(screen.getByRole('button', { name: '创建演示任务' })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: '移除已选文件' })); expect(screen.queryByText('paper.bib')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '创建演示任务' })).toBeDisabled();
  await userEvent.upload(input, file); await waitFor(() => expect(screen.getByRole('button', { name: '创建演示任务' })).toBeEnabled());
});
it('preserves a custom task name when reselecting', async () => {
  mount(); drop([bib()]); await userEvent.clear(screen.getByLabelText('任务名称')); await userEvent.type(screen.getByLabelText('任务名称'), '自定义任务'); drop([bib('replacement.bib')]);
  await waitFor(() => expect(screen.getByRole('button', { name: '创建演示任务' })).toBeEnabled());
  expect(screen.getByLabelText('任务名称')).toHaveValue('自定义任务'); expect(screen.getByText('replacement.bib')).toBeVisible();
});
it('rejects invalid or multiple dropped files and recovers on a new choice', async () => {
  mount(); drop([new File(['text'], 'wrong.txt')]); expect(screen.getByText('请选择 .bib 文件。')).toBeVisible(); expect(screen.getByRole('button', { name: '创建演示任务' })).toBeDisabled();
  drop([bib(), bib('second.bib')]); expect(screen.getByText('请一次选择一份 Bib 文件。')).toBeVisible();
  drop([bib()]); await waitFor(() => expect(screen.getByRole('button', { name: '创建演示任务' })).toBeEnabled()); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('rejects empty, oversized and invalid UTF-8 input', async () => {
  mount(); drop([new File([], 'empty.bib')]); expect(screen.getByText('文件为空，请重新选择。')).toBeVisible();
  drop([new File([new Uint8Array(5242881)], 'large.bib')]); expect(screen.getByText('文件不能超过 5.0 MB。')).toBeVisible();
  drop([new File([new Uint8Array([255, 254])], 'broken.bib')]); await screen.findByText('无法读取 UTF-8 文件，请检查编码后重新选择。'); expect(screen.getByRole('button', { name: '创建演示任务' })).toBeDisabled();
});
it('allows retry after a server failure without reselecting the file', async () => {
  fetchMock.mockImplementation(async (_url: string, options?: RequestInit) => options?.method === 'POST' ? response({ error: { message: '服务暂不可用' } }, 503) : response(settings));
  mount(); drop([bib()]); await waitFor(() => expect(screen.getByRole('button', { name: '创建演示任务' })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: '创建演示任务' })); expect(await screen.findByText('服务暂不可用')).toBeVisible();
  expect(screen.getByRole('button', { name: '创建演示任务' })).toBeEnabled(); await userEvent.click(screen.getByRole('button', { name: '创建演示任务' }));
  expect(fetchMock.mock.calls.filter(call => call[1]?.method === 'POST')).toHaveLength(2);
});

const budgetWarning = { error: { code: 'budget_confirmation_required', message: '请选择预算处理方式', details: { entry_count: 10, entry_request_budget: 18, request_budget: 100, maximum_requests: 180 } } };

it('requires an explicit budget choice and continues with the same limits and file', async () => {
  fetchMock.mockImplementation(async (_url: string, options?: RequestInit) => options?.method === 'POST'
    ? response(budgetWarning, 409) : response(settings));
  mount(); const file = bib(); drop([file]);
  await waitFor(() => expect(screen.getByRole('button', { name: '创建演示任务' })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: '创建演示任务' }));
  expect(await screen.findByRole('heading', { name: '理论请求上限超过任务预算' })).toBeVisible();
  expect(screen.getByText('10 条 × 每条最多 18 次 = 180 次，超过任务请求总上限 100 次。')).toBeVisible();
  const posts = () => fetchMock.mock.calls.filter(call => call[1]?.method === 'POST');
  expect(posts()).toHaveLength(1);
  const first = posts()[0][1].body as FormData;
  expect(first.get('confirm_budget')).toBeNull();
  // Refreshed global defaults must not change the limits the user is confirming.
  fetchMock.mockImplementation(async (_url: string, options?: RequestInit) => options?.method === 'POST'
    ? response(budgetWarning, 409) : response({ ...settings, runtime: { ...settings.runtime, entry_request_budget: 40, request_budget: 200 } }));
  fireEvent(window, new Event('online'));
  await waitFor(() => expect(fetchMock.mock.calls.filter(call => !call[1]?.method)).toHaveLength(2));
  await userEvent.click(screen.getByRole('button', { name: '按当前预算继续' }));
  await waitFor(() => expect(posts()).toHaveLength(2));
  const second = posts()[1][1].body as FormData;
  expect(second.get('confirm_budget')).toBe('true');
  expect(second.get('config')).toBe(first.get('config'));
  expect(second.get('file')).toBe(first.get('file'));
});
