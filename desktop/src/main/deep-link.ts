export const PIXELPILOT_PROTOCOL = 'pixelpilot';

export type PixelPilotDeepLinkPayload = {
  code: string;
  state: string;
};

export function findDeepLinkArg(argv: string[]): string | null {
  return argv.find((value) => String(value || '').startsWith(`${PIXELPILOT_PROTOCOL}://`)) ?? null;
}

export function parsePixelPilotDeepLink(rawUrl: string): PixelPilotDeepLinkPayload | null {
  const value = String(rawUrl || '').trim();
  if (!value.startsWith(`${PIXELPILOT_PROTOCOL}://`)) {
    return null;
  }

  try {
    const parsed = new URL(value);
    const code = String(parsed.searchParams.get('code') || '').trim();
    const state = String(parsed.searchParams.get('state') || '').trim();
    if (!code || !state) {
      return null;
    }
    return { code, state };
  } catch {
    return null;
  }
}
