import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { User } from '@/types/auth';
import { tokenStorage } from '@/api/client';

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: () => boolean;
  login: (token: string, user: User) => void;
  logout: () => void;
  setUser: (user: User) => void;
}

// 用户认证状态：持久化到 localStorage
export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      token: null,
      isAuthenticated: () => Boolean(get().token),
      login: (token, user) => {
        tokenStorage.set(token);
        set({ token, user });
      },
      logout: () => {
        tokenStorage.clear();
        set({ token: null, user: null });
      },
      setUser: (user) => set({ user }),
    }),
    {
      name: 'ann_search_auth',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ user: state.user, token: state.token }),
    },
  ),
);
