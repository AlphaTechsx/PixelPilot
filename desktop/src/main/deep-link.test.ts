import { describe, expect, it } from 'vitest';
import { findDeepLinkArg, parsePixelPilotDeepLink } from './deep-link.js';

describe('deep-link helpers', () => {
  it('finds a pixelpilot deep link in argv', () => {
    expect(findDeepLinkArg(['app.exe', '--flag', 'pixelpilot://auth?code=abc&state=xyz'])).toBe(
      'pixelpilot://auth?code=abc&state=xyz'
    );
  });

  it('parses a valid deep link payload', () => {
    expect(parsePixelPilotDeepLink('pixelpilot://auth?code=abc&state=xyz')).toEqual({
      code: 'abc',
      state: 'xyz'
    });
  });

  it('rejects invalid deep links', () => {
    expect(parsePixelPilotDeepLink('pixelpilot://auth?code=abc')).toBeNull();
    expect(parsePixelPilotDeepLink('https://example.com')).toBeNull();
  });
});
