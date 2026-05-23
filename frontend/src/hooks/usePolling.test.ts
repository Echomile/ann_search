import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { usePolling } from './usePolling';

/**
 * usePolling Hook 行为验证。
 *
 * 使用 vi.useFakeTimers 控制 setInterval；
 * 注意：hook 内对 immediate 的同步 tick 也走 async 路径，
 * 因此需要 await Promise.resolve() / vi.runAllTicks() 排空微任务以推进 callback 执行。
 */
describe('usePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('enabled=true 按 interval 重复触发 callback', async () => {
    const cb = vi.fn();
    renderHook(() => usePolling(cb, { interval: 1000, enabled: true }));

    expect(cb).toHaveBeenCalledTimes(0);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(cb).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(cb).toHaveBeenCalledTimes(4);
  });

  it('enabled=false 不会触发 callback', async () => {
    const cb = vi.fn();
    renderHook(() => usePolling(cb, { interval: 500, enabled: false }));

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(cb).not.toHaveBeenCalled();
  });

  it('enabled 从 true 切换到 false 后停止触发', async () => {
    const cb = vi.fn();
    const { rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        usePolling(cb, { interval: 1000, enabled }),
      { initialProps: { enabled: true } },
    );

    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(cb).toHaveBeenCalledTimes(2);

    rerender({ enabled: false });

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(cb).toHaveBeenCalledTimes(2);
  });

  it('immediate=true 在挂载后立即触发一次再按周期', async () => {
    const cb = vi.fn();
    renderHook(() => usePolling(cb, { interval: 1000, enabled: true, immediate: true }));

    await act(async () => {
      await Promise.resolve();
    });
    expect(cb).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(cb).toHaveBeenCalledTimes(2);
  });

  it('卸载时清理 timer，不再触发 callback', async () => {
    const cb = vi.fn();
    const { unmount } = renderHook(() =>
      usePolling(cb, { interval: 1000, enabled: true }),
    );

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(cb).toHaveBeenCalledTimes(1);

    unmount();

    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(cb).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('callback 抛错不会中断后续轮询', async () => {
    const cb = vi
      .fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValue(undefined);
    renderHook(() => usePolling(cb, { interval: 1000, enabled: true }));

    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(cb).toHaveBeenCalledTimes(2);
  });
});
