import fs from 'node:fs';
import path from 'node:path';
import type { RuntimeSnapshot, StartupDefaultsSnapshot } from '../shared/types.js';

type OperationMode = StartupDefaultsSnapshot['operationMode'];
type VisionMode = StartupDefaultsSnapshot['visionMode'];

export type StartupDefaultsInput = {
  operationMode: OperationMode;
  visionMode: VisionMode;
};

function normalizeOperationMode(value: unknown): OperationMode {
  const key = String(value || '').trim().toUpperCase();
  if (key === 'GUIDANCE' || key === 'SAFE' || key === 'AUTO') {
    return key;
  }
  return 'AUTO';
}

function normalizeVisionMode(value: unknown): VisionMode {
  const key = String(value || '').trim().toUpperCase();
  if (key === 'ROBO' || key === 'OCR') {
    return key;
  }
  return 'OCR';
}

function normalizePersisted(payload: unknown): StartupDefaultsInput | null {
  if (!payload || typeof payload !== 'object') {
    return null;
  }
  const candidate = payload as Record<string, unknown>;
  return {
    operationMode: normalizeOperationMode(candidate.operationMode),
    visionMode: normalizeVisionMode(candidate.visionMode),
  };
}

function defaultsFromRuntime(snapshot: RuntimeSnapshot | null | undefined): StartupDefaultsInput {
  if (snapshot) {
    return {
      operationMode: normalizeOperationMode(snapshot.operationMode),
      visionMode: normalizeVisionMode(snapshot.visionMode),
    };
  }
  return {
    operationMode: 'AUTO',
    visionMode: 'OCR',
  };
}

export class StartupDefaultsStore {
  private readonly filePath: string;
  private loaded = false;
  private cached: StartupDefaultsInput | null = null;

  constructor(userDataDir: string) {
    this.filePath = path.join(userDataDir, 'startup-defaults.json');
  }

  loadPersisted(): StartupDefaultsInput | null {
    if (this.loaded) {
      return this.cached;
    }

    this.loaded = true;
    try {
      if (!fs.existsSync(this.filePath)) {
        this.cached = null;
        return null;
      }
      const raw = fs.readFileSync(this.filePath, 'utf-8');
      const parsed = raw ? JSON.parse(raw) : null;
      this.cached = normalizePersisted(parsed);
      return this.cached;
    } catch {
      this.cached = null;
      return null;
    }
  }

  save(input: StartupDefaultsInput): StartupDefaultsSnapshot {
    const normalized: StartupDefaultsInput = {
      operationMode: normalizeOperationMode(input.operationMode),
      visionMode: normalizeVisionMode(input.visionMode),
    };

    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, JSON.stringify(normalized, null, 2), 'utf-8');
    this.cached = normalized;
    this.loaded = true;
    return {
      ...normalized,
      hasPersisted: true,
      source: 'persisted',
    };
  }

  resolve(snapshot: RuntimeSnapshot | null | undefined): StartupDefaultsSnapshot {
    const persisted = this.loadPersisted();
    if (persisted) {
      return {
        ...persisted,
        hasPersisted: true,
        source: 'persisted',
      };
    }

    const runtimeDefaults = defaultsFromRuntime(snapshot);
    return {
      ...runtimeDefaults,
      hasPersisted: false,
      source: snapshot ? 'runtime' : 'fallback',
    };
  }
}
