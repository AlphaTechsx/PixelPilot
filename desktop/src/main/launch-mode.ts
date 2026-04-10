export const BACKGROUND_STARTUP_ARG = '--background-startup';

export type LaunchMode = {
  backgroundStartup: boolean;
};

export function hasLaunchArg(argv: string[], expected: string): boolean {
  const normalized = String(expected || '').trim().toLowerCase();
  if (!normalized) {
    return false;
  }

  return argv.some((value) => String(value || '').trim().toLowerCase() === normalized);
}

export function parseLaunchMode(argv: string[]): LaunchMode {
  return {
    backgroundStartup: hasLaunchArg(argv, BACKGROUND_STARTUP_ARG),
  };
}
