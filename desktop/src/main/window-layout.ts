import type { WindowKind } from '../shared/types.js';

export type ScreenWorkArea = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type WindowBounds = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type WindowSize = {
  width: number;
  height: number;
};

const EDGE_PADDING = 24;
const OVERLAY_TOP_OFFSET = 24;
const SIDECAR_TOP_OFFSET = 96;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function normalizeWindowSize(kind: WindowKind, workArea: ScreenWorkArea, size: WindowSize): WindowSize {
  const maxWidth = Math.max(240, workArea.width - EDGE_PADDING * 2);
  const maxHeight = Math.max(140, workArea.height - EDGE_PADDING * 2);

  if (kind === 'notch') {
    return {
      width: clamp(Math.round(size.width), 260, Math.min(420, maxWidth)),
      height: clamp(Math.round(size.height), 76, Math.min(140, maxHeight))
    };
  }

  if (kind === 'sidecar') {
    return {
      width: clamp(Math.round(size.width), 320, Math.min(520, maxWidth)),
      height: clamp(Math.round(size.height), 260, Math.min(760, maxHeight))
    };
  }

  if (kind === 'settings') {
    return {
      width: clamp(Math.round(size.width), 200, Math.min(280, maxWidth)),
      height: clamp(Math.round(size.height), 180, Math.min(360, maxHeight))
    };
  }

  return {
    width: clamp(Math.round(size.width), 620, maxWidth),
    height: clamp(Math.round(size.height), 84, Math.min(820, maxHeight))
  };
}

export function getAnchoredWindowBounds(kind: WindowKind, workArea: ScreenWorkArea, requested: WindowSize): WindowBounds {
  const size = normalizeWindowSize(kind, workArea, requested);

  if (kind === 'notch') {
    return {
      x: Math.round(workArea.x + (workArea.width - size.width) / 2),
      y: workArea.y,
      width: size.width,
      height: size.height
    };
  }

  if (kind === 'sidecar') {
    return {
      x: workArea.x + workArea.width - size.width - EDGE_PADDING,
      y: workArea.y + SIDECAR_TOP_OFFSET,
      width: size.width,
      height: size.height
    };
  }

  return {
    x: Math.round(workArea.x + (workArea.width - size.width) / 2),
    y: workArea.y + OVERLAY_TOP_OFFSET,
    width: size.width,
    height: size.height
  };
}
