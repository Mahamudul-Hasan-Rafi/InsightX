'use client';

// BFF Authorization Code + PKCE S256 flow.
//
// Authentication is entirely server-driven:
//   1. This component calls GET /api/auth/me (credentials: include).
//   2. If 401 → redirect browser to GET /api/auth/login (backend starts PKCE flow).
//   3. After Keycloak callback, backend sets HttpOnly cookies and redirects back here.
//   4. /me is called again and returns the decoded identity — now we render children.
//
// Tokens never touch JavaScript; they live exclusively in HttpOnly cookies.

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';

import { getAuthLogin, getAuthLogout, getAuthMe } from '@/config/url.config';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AuthUser {
  sub?: string;
  username?: string;
  name?: string;
  given_name?: string;
  email?: string;
  roles?: string[];
  tenant_id?: string;
}

interface AuthContextValue {
  user: AuthUser;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export default function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    fetch(getAuthMe, { credentials: 'include' })
      .then(async (res) => {
        if (res.status === 401) {
          // Session absent or expired — start a fresh BFF login flow.
          window.location.href = getAuthLogin;
          return;
        }
        if (!res.ok) {
          throw new Error(`Auth check returned HTTP ${res.status}`);
        }
        setUser(await res.json());
      })
      .catch((err) => {
        console.error('[auth] Could not reach auth service:', err);
        setFailed('Could not reach the authentication server. Please try refreshing.');
      });
  }, []);

  if (failed) {
    return (
      <div style={fullscreen}>
        <div style={{ textAlign: 'center' }}>
          <p style={{ fontWeight: 700, marginBottom: 6 }}>Sign-in unavailable</p>
          <p style={{ opacity: 0.7, fontSize: 13 }}>{failed}</p>
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <div style={fullscreen}>
        <span style={{ opacity: 0.6, fontSize: 14 }}>Signing you in…</span>
      </div>
    );
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        logout: () => { window.location.href = getAuthLogout; },
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

const fullscreen: React.CSSProperties = {
  minHeight: '100vh',
  display: 'grid',
  placeItems: 'center',
};

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>');
  return ctx;
}
