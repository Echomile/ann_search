import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useAuthStore } from './authStore';
import type { User } from '@/types/auth';

const mockUser: User = {
  id: 1,
  username: 'alice',
  role: 'admin',
  created_at: '2026-01-01T00:00:00Z',
};

/**
 * authStore 行为验证：登录态切换、tokenStorage 持久化、partialize 写入 localStorage。
 *
 * 通过 setState 重置 store 到初始态，jsdom 自带 localStorage 即可验证 persist middleware。
 */
describe('useAuthStore', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({ user: null, token: null });
  });

  afterEach(() => {
    localStorage.clear();
    useAuthStore.setState({ user: null, token: null });
  });

  it('初始状态未登录', () => {
    const state = useAuthStore.getState();
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
    expect(state.isAuthenticated()).toBe(false);
  });

  it('login 后写入 token/user 且 isAuthenticated 为 true', () => {
    useAuthStore.getState().login('jwt-token-xyz', mockUser);

    const state = useAuthStore.getState();
    expect(state.token).toBe('jwt-token-xyz');
    expect(state.user).toEqual(mockUser);
    expect(state.isAuthenticated()).toBe(true);
    expect(localStorage.getItem('ann_search_token')).toBe('jwt-token-xyz');
  });

  it('logout 后 token/user 清空且 isAuthenticated 为 false', () => {
    useAuthStore.getState().login('jwt-token-xyz', mockUser);
    expect(useAuthStore.getState().isAuthenticated()).toBe(true);

    useAuthStore.getState().logout();

    const state = useAuthStore.getState();
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
    expect(state.isAuthenticated()).toBe(false);
    expect(localStorage.getItem('ann_search_token')).toBeNull();
  });

  it('setUser 只更新 user 字段，不动 token', () => {
    useAuthStore.getState().login('jwt-token-xyz', mockUser);
    const updated: User = { ...mockUser, username: 'bob' };

    useAuthStore.getState().setUser(updated);

    const state = useAuthStore.getState();
    expect(state.user?.username).toBe('bob');
    expect(state.token).toBe('jwt-token-xyz');
  });

  it('persist middleware 写入 localStorage 仅含 user/token', () => {
    useAuthStore.getState().login('persisted-token', mockUser);

    const raw = localStorage.getItem('ann_search_auth');
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw as string) as { state: Record<string, unknown> };
    expect(parsed.state.token).toBe('persisted-token');
    expect(parsed.state.user).toEqual(mockUser);
    // partialize 不应持久化函数字段
    expect(parsed.state.isAuthenticated).toBeUndefined();
    expect(parsed.state.login).toBeUndefined();
  });
});
