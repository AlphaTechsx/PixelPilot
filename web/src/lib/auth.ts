export type DesktopCodeResponse = {
  code: string;
  expiresIn: number;
};

export function getBackendUrl(): string {
  const value = String(import.meta.env.VITE_BACKEND_URL || '').trim();
  if (!value) {
    throw new Error('VITE_BACKEND_URL is not configured.');
  }
  return value.replace(/\/+$/, '');
}

export function buildGoogleStartUrl(desktopState: string, mode: 'signin' | 'signup'): string {
  const backendUrl = getBackendUrl();
  const query = new URLSearchParams({
    desktop_state: desktopState,
    mode
  });
  return `${backendUrl}/auth/google/start?${query.toString()}`;
}

export function buildAuthCompleteHash(code: string, state: string): string {
  return `#code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`;
}

export function buildDesktopDeepLink(code: string, state: string): string {
  const query = new URLSearchParams({ code, state });
  return `pixelpilot://auth?${query.toString()}`;
}

export function parseHashPayload(hash: string): { code: string; state: string } | null {
  const normalized = String(hash || '').startsWith('#') ? String(hash).slice(1) : String(hash || '');
  const params = new URLSearchParams(normalized);
  const code = String(params.get('code') || '').trim();
  const state = String(params.get('state') || '').trim();
  if (!code || !state) {
    return null;
  }
  return { code, state };
}

export async function postJson<T>(
  path: string,
  payload: unknown,
  token?: string
): Promise<T> {
  const response = await fetch(`${getBackendUrl()}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    },
    body: JSON.stringify(payload)
  });

  const rawText = await response.text();
  let body: Record<string, unknown> = {};
  if (rawText) {
    try {
      body = JSON.parse(rawText) as Record<string, unknown>;
    } catch {
      body = { detail: rawText };
    }
  }
  if (!response.ok) {
    throw new Error(String(body.detail || 'Request failed'));
  }
  return body as T;
}
