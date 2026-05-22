import { useEffect, useRef } from 'react';

interface UsePollingOptions {
  interval: number;
  enabled: boolean;
  immediate?: boolean;
}

/**
 * 轻量轮询 Hook：在 enabled 为 true 时按 interval 周期触发 callback。
 *
 * - 卸载或依赖变更时自动停止；
 * - callback 异步异常不会中断后续轮询；
 * - 通过 ref 保存最新回调，避免 stale closure。
 */
export function usePolling(callback: () => void | Promise<void>, options: UsePollingOptions): void {
  const { interval, enabled, immediate = false } = options;
  const cbRef = useRef(callback);
  cbRef.current = callback;

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        await cbRef.current();
      } catch {
        // 轮询失败静默忽略，由调用方按需提示
      }
    };
    if (immediate) void tick();
    const timer = window.setInterval(() => {
      if (!cancelled) void tick();
    }, interval);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [enabled, interval, immediate]);
}
