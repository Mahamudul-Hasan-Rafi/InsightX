// web/lib/utils/auth-fetch.utils.ts
//
// Auth-aware fetch() wrapper for the BFF cookie flow.
//
// With BFF, tokens live exclusively in HttpOnly cookies set by the backend.
// The browser sends them automatically on every same-site request; all this
// wrapper needs to do is add `credentials: 'include'` so the browser attaches
// those cookies on cross-origin calls to the backend (both ports are localhost,
// so cookies are same-site and SameSite=Lax allows this).
//
// On 401: the session cookie has expired or been revoked server-side.
// Redirect to the backend BFF login to start a fresh PKCE flow.

import { getAuthLogin } from '@/config/url.config';

export async function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const response = await fetch(input, {
    ...init,
    credentials: 'include',
  });

  if (response.status === 401 && typeof window !== 'undefined') {
    window.location.href = getAuthLogin;
  }

  return response;
}
