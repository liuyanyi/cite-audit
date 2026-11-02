import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SearchInput } from '../components/ui';

afterEach(() => vi.useRealTimers());

it('keeps IME preedit intact across renders and submits only the committed text', () => {
  vi.useFakeTimers();
  const change = vi.fn();
  const { rerender } = render(<SearchInput value="" onChange={change} debounceMs={300} />);
  const input = screen.getByRole('textbox');
  fireEvent.compositionStart(input);
  for (const value of ['z', 'zh', 'zho', 'zhon', 'zhong']) {
    fireEvent.change(input, { target: { value } });
    rerender(<SearchInput value="" onChange={change} debounceMs={300} />);
    expect(input).toHaveValue(value);
    act(() => vi.advanceTimersByTime(400));
  }
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true, keyCode: 229 });
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: '中文' } });
  fireEvent.compositionEnd(input, { data: '中文' });
  // Browsers may emit a final input/change after compositionend.
  fireEvent.change(input, { target: { value: '中文' } });
  act(() => vi.advanceTimersByTime(300));
  expect(change.mock.calls).toEqual([['中文']]);
  rerender(<SearchInput value="中文" onChange={change} debounceMs={300} />);
  expect(input).toHaveValue('中文');
});

it('cancels preedit timers, flushes Enter and clear immediately, and cancels on unmount', () => {
  vi.useFakeTimers();
  const change = vi.fn();
  const { unmount } = render(<SearchInput value="old" onChange={change} debounceMs={300} />);
  const input = screen.getByRole('textbox');
  fireEvent.change(input, { target: { value: 'z' } });
  fireEvent.compositionStart(input);
  act(() => vi.advanceTimersByTime(400));
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: '中' } });
  fireEvent.compositionEnd(input);
  fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 });
  expect(change).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(change.mock.calls).toEqual([['中']]);
  fireEvent.change(input, { target: { value: 'pending' } });
  fireEvent.click(screen.getByRole('button', { name: '清除搜索' }));
  expect(input).toHaveValue('');
  expect(input).toHaveFocus();
  expect(change.mock.calls).toEqual([['中'], ['']]);
  act(() => vi.advanceTimersByTime(400));
  expect(change).toHaveBeenCalledTimes(2);
  fireEvent.change(input, { target: { value: 'unmounted' } });
  unmount();
  act(() => vi.advanceTimersByTime(400));
  expect(change).toHaveBeenCalledTimes(2);
});

it('does not replay an acknowledged query over newer text, and follows external navigation', () => {
  vi.useFakeTimers();
  const change = vi.fn();
  const { rerender } = render(<SearchInput value="old" onChange={change} debounceMs={300} />);
  const input = screen.getByRole('textbox');
  fireEvent.change(input, { target: { value: 'first' } });
  act(() => vi.advanceTimersByTime(300));
  fireEvent.change(input, { target: { value: 'newer' } });
  rerender(<SearchInput value="first" onChange={change} debounceMs={300} />);
  expect(input).toHaveValue('newer');
  rerender(<SearchInput value="external" onChange={change} debounceMs={300} />);
  expect(input).toHaveValue('external');
  act(() => vi.advanceTimersByTime(300));
  expect(change.mock.calls).toEqual([['first']]);
});
