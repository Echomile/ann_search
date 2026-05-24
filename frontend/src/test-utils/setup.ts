/**
 * Vitest 全局 setup：补全 jsdom 未实现的浏览器 API。
 *
 * antd 5 在 Grid/Slider/Carousel 等组件中调用：
 *   - window.matchMedia（响应式断点订阅）；
 *   - ResizeObserver（Slider 等容器尺寸监听）；
 *   - IntersectionObserver（部分懒加载场景）。
 * jsdom 默认未提供，此处统一 polyfill 为空实现，避免组件渲染时抛错。
 */
import { vi } from 'vitest';

// matchMedia ----------------------------------------------------------------
if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

// ResizeObserver ------------------------------------------------------------
class MockResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
if (typeof globalThis.ResizeObserver === 'undefined') {
  (globalThis as unknown as { ResizeObserver: typeof MockResizeObserver }).ResizeObserver =
    MockResizeObserver;
}

// IntersectionObserver ------------------------------------------------------
class MockIntersectionObserver {
  root: Element | null = null;
  rootMargin = '';
  thresholds: readonly number[] = [];
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  takeRecords = vi.fn().mockReturnValue([]);
}
if (typeof globalThis.IntersectionObserver === 'undefined') {
  (
    globalThis as unknown as { IntersectionObserver: typeof MockIntersectionObserver }
  ).IntersectionObserver = MockIntersectionObserver;
}

// scrollIntoView ------------------------------------------------------------
// antd Select listbox 滚动定位时会调用，jsdom 默认未实现
if (
  typeof window !== 'undefined' &&
  typeof window.HTMLElement.prototype.scrollIntoView === 'undefined'
) {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
}

// getComputedStyle 在 jsdom 下可用但偶有 issue；这里不动以免影响快照对比
