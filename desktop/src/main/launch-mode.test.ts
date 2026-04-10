import { describe, expect, it } from 'vitest';
import { BACKGROUND_STARTUP_ARG, hasLaunchArg, parseLaunchMode } from './launch-mode.js';

describe('launch-mode helpers', () => {
  it('detects the background startup argument', () => {
    expect(hasLaunchArg(['PixelPilot.exe', BACKGROUND_STARTUP_ARG], BACKGROUND_STARTUP_ARG)).toBe(true);
  });

  it('matches arguments case-insensitively', () => {
    expect(hasLaunchArg(['PixelPilot.exe', '--BACKGROUND-STARTUP'], BACKGROUND_STARTUP_ARG)).toBe(true);
  });

  it('parses launch mode flags from argv', () => {
    expect(parseLaunchMode(['PixelPilot.exe', BACKGROUND_STARTUP_ARG])).toEqual({
      backgroundStartup: true,
    });
    expect(parseLaunchMode(['PixelPilot.exe'])).toEqual({
      backgroundStartup: false,
    });
  });
});
