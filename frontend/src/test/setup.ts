import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
afterEach(cleanup);

// jsdom omits layout/pointer APIs used by Radix focus and positioning primitives.
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
HTMLElement.prototype.hasPointerCapture = () => false;
HTMLElement.prototype.setPointerCapture = () => {};
HTMLElement.prototype.releasePointerCapture = () => {};
HTMLElement.prototype.scrollIntoView = vi.fn();
if (!globalThis.PointerEvent) globalThis.PointerEvent = MouseEvent as typeof PointerEvent;
