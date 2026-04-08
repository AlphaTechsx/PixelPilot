import { describe, expect, it } from 'vitest';
import { buildAuthCompleteHash, buildDesktopDeepLink, parseHashPayload } from './auth';

describe('web auth helpers', () => {
  it('builds and parses the auth completion hash', () => {
    expect(parseHashPayload(buildAuthCompleteHash('code-1', 'state-1'))).toEqual({
      code: 'code-1',
      state: 'state-1'
    });
  });

  it('builds the desktop deep link', () => {
    expect(buildDesktopDeepLink('code-1', 'state-1')).toBe(
      'pixelpilot://auth?code=code-1&state=state-1'
    );
  });

  it('rejects incomplete hash payloads', () => {
    expect(parseHashPayload('#code=only')).toBeNull();
  });
});
