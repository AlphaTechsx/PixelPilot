import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import net, { type Server as NetServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import WebSocket from 'ws';

const require = createRequire(import.meta.url);
const { app } = require('electron') as typeof import('electron');

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const BRIDGE_START_TIMEOUT_MS = 30000;
const BRIDGE_POLL_INTERVAL_MS = 150;
type ReleaseConfig = {
  backendUrl?: string;
  webUrl?: string;
};

function prependEnvPath(currentValue: string | undefined, nextEntry: string): string {
  if (!currentValue) {
    return nextEntry;
  }

  const entries = currentValue
    .split(path.delimiter)
    .map((entry) => entry.trim())
    .filter(Boolean);

  if (entries.includes(nextEntry)) {
    return currentValue;
  }

  return [nextEntry, ...entries].join(path.delimiter);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function runtimeDiagnosticsHint(): string {
  if (app.isPackaged) {
    const localAppData = process.env.LOCALAPPDATA;
    if (localAppData) {
      return ` See ${path.join(localAppData, 'PixelPilot', 'logs', 'runtime-bootstrap.log')} for startup details.`;
    }
  }

  const repoRoot = path.resolve(__dirname, '..', '..', '..');
  return ` See ${path.join(repoRoot, 'logs', 'runtime-bootstrap.log')} for startup details.`;
}

function loadPackagedReleaseConfig(): ReleaseConfig {
  if (!app.isPackaged) {
    return {};
  }

  try {
    const configPath = path.join(process.resourcesPath, 'release-config.json');
    if (!fs.existsSync(configPath)) {
      return {};
    }

    const raw = fs.readFileSync(configPath, 'utf8');
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      return {};
    }

    return {
      backendUrl:
        typeof parsed.backendUrl === 'string' ? parsed.backendUrl.trim() : undefined,
      webUrl: typeof parsed.webUrl === 'string' ? parsed.webUrl.trim() : undefined
    };
  } catch {
    return {};
  }
}

async function canConnect(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new WebSocket(url);
    let settled = false;

    const finish = (connected: boolean): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      socket.removeAllListeners();
      if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
        socket.close();
      }
      resolve(connected);
    };

    const timer = setTimeout(() => finish(false), 750);

    socket.once('open', () => finish(true));
    socket.once('error', () => finish(false));
    socket.once('unexpected-response', () => finish(false));
  });
}

async function getFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server: NetServer = net.createServer();
    server.unref();
    (server as unknown as { on: (event: string, listener: (error: Error) => void) => void }).on(
      'error',
      (error: Error) => reject(error)
    );
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      server.close(() => {
        if (!address || typeof address === 'string') {
          reject(new Error('Failed to resolve a free port.'));
          return;
        }
        resolve(address.port);
      });
    });
  });
}

export class RuntimeProcessManager {
  private child: ChildProcessWithoutNullStreams | null = null;
  private token = '';
  private port = 0;
  private launchError: Error | null = null;
  private endpoints: { controlUrl: string; sidecarUrl: string } | null = null;

  isRunning(): boolean {
    return Boolean(this.child && this.child.exitCode === null);
  }

  getBridgeEndpoints(): { controlUrl: string; sidecarUrl: string } | null {
    return this.endpoints;
  }

  async start(): Promise<{ controlUrl: string; sidecarUrl: string }> {
    this.port = await getFreePort();
    this.token = crypto.randomUUID();
    const launch = this.resolveRuntimeLaunch();
    const releaseConfig = loadPackagedReleaseConfig();

    const env = {
      ...process.env,
      ...launch.env,
      ...(releaseConfig.backendUrl && !process.env.BACKEND_URL
        ? { BACKEND_URL: releaseConfig.backendUrl }
        : {}),
      ...(releaseConfig.webUrl && !process.env.WEB_URL ? { WEB_URL: releaseConfig.webUrl } : {}),
      PIXELPILOT_ELECTRON_BRIDGE_HOST: '127.0.0.1',
      PIXELPILOT_ELECTRON_BRIDGE_PORT: String(this.port),
      PIXELPILOT_ELECTRON_BRIDGE_TOKEN: this.token
    };

    this.launchError = null;
    this.child = spawn(launch.command, launch.args, {
      cwd: launch.cwd,
      env,
      stdio: 'pipe'
    });
    (
      this.child as unknown as {
        on: (event: string, listener: (error: Error) => void) => void;
      }
    ).on('error', (error: Error) => {
      this.launchError = error instanceof Error ? error : new Error(String(error));
    });

    this.child.stdout.on('data', (chunk) => {
      process.stdout.write(`[runtime] ${chunk}`);
    });
    this.child.stderr.on('data', (chunk) => {
      process.stderr.write(`[runtime] ${chunk}`);
    });

    try {
      await this.waitForBridgeServer();
    } catch (error) {
      this.stop();
      throw error;
    }

    this.endpoints = {
      controlUrl: `ws://127.0.0.1:${this.port}/control?token=${this.token}`,
      sidecarUrl: `ws://127.0.0.1:${this.port}/sidecar?token=${this.token}`
    };

    return this.endpoints;
  }

  stop(): void {
    this.child?.kill();
    this.child = null;
    this.endpoints = null;
  }

  private async waitForBridgeServer(): Promise<void> {
    const deadline = Date.now() + BRIDGE_START_TIMEOUT_MS;

    while (Date.now() < deadline) {
      if (this.launchError) {
        throw this.launchError;
      }
      if (!this.child) {
        throw new Error('PixelPilot runtime process was not started.');
      }
      if (this.child.exitCode !== null) {
        const signal = this.child.signalCode ? ` (signal: ${this.child.signalCode})` : '';
        throw new Error(
          `PixelPilot runtime exited before the bridge was ready (code ${this.child.exitCode}${signal}).${runtimeDiagnosticsHint()}`
        );
      }
      if (await canConnect(`ws://127.0.0.1:${this.port}/control?token=${this.token}`)) {
        return;
      }
      await delay(BRIDGE_POLL_INTERVAL_MS);
    }

    throw new Error(`Timed out waiting for the PixelPilot runtime bridge to start.${runtimeDiagnosticsHint()}`);
  }

  private resolveRuntimeLaunch(): {
    command: string;
    args: string[];
    cwd?: string;
    env?: Record<string, string>;
  } {
    const explicitRuntime = process.env.PIXELPILOT_RUNTIME_EXE;
    if (explicitRuntime) {
      return { command: explicitRuntime, args: [] };
    }

    if (app.isPackaged) {
      const packagedRuntime = path.join(
        process.resourcesPath,
        'runtime',
        'pixelpilot-runtime',
        'pixelpilot-runtime.exe'
      );
      return { command: packagedRuntime, args: [], cwd: path.dirname(packagedRuntime) };
    }

    const repoRoot = path.resolve(__dirname, '..', '..', '..');
    const pythonExe = path.join(repoRoot, 'venv', 'Scripts', 'python.exe');
    return {
      command: pythonExe,
      args: ['-m', 'runtime'],
      cwd: repoRoot,
      env: {
        PYTHONPATH: prependEnvPath(process.env.PYTHONPATH, path.join(repoRoot, 'src'))
      }
    };
  }
}
