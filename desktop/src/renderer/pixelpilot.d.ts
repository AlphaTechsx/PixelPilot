import type { PixelPilotApi } from '@shared/types.js';

declare global {
  interface Window {
    pixelPilot: PixelPilotApi;
  }
}

export {};
