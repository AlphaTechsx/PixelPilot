import { createRequire } from 'node:module';
import { RuntimeBridgeClient } from './bridge-client.js';
import { RuntimeProcessManager } from './runtime-process.js';
import { WindowManager } from './window-manager.js';
import type { RuntimeEventEnvelope } from '../shared/types.js';

const require = createRequire(import.meta.url);
const { app } = require('electron') as typeof import('electron');

let runtimeProcess: RuntimeProcessManager | null = null;
let bridgeClient: RuntimeBridgeClient | null = null;
let windowManager: WindowManager | null = null;

async function bootstrap(): Promise<void> {
  runtimeProcess = new RuntimeProcessManager();
  const endpoints = await runtimeProcess.start();
  bridgeClient = new RuntimeBridgeClient(endpoints.controlUrl, endpoints.sidecarUrl);
  windowManager = new WindowManager((method, payload) => bridgeClient!.sendCommand(method, payload));

  bridgeClient.on('snapshot', (snapshot) => {
    windowManager?.applySnapshot(snapshot);
  });

  bridgeClient.on('event', (envelope: RuntimeEventEnvelope) => {
    if (envelope.method !== 'state.snapshot' && envelope.method !== 'state.updated') {
      windowManager?.broadcastEvent(envelope);
    }
  });

  bridgeClient.on('request', async (envelope: RuntimeEventEnvelope) => {
    const payload = await windowManager?.handleRuntimeRequest(envelope);
    bridgeClient?.respond(envelope.id, envelope.method, payload ?? {});
  });

  bridgeClient.on('sidecar-frame', (frame) => {
    windowManager?.sendSidecarFrame(frame);
  });

  bridgeClient.on('runtime-error', (payload) => {
    windowManager?.broadcastEvent({
      id: crypto.randomUUID(),
      kind: 'error',
      method: 'runtime.error',
      payload,
      protocolVersion: 1
    });
  });

  windowManager.createWindows();
  bridgeClient.start();
}

app.whenReady().then(() => {
  void bootstrap().catch((error) => {
    console.error('Failed to bootstrap PixelPilot Electron.', error);
    app.exit(1);
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  void bridgeClient?.sendCommand('runtime.shutdown').catch(() => undefined);
  bridgeClient?.dispose();
  windowManager?.dispose();
  runtimeProcess?.stop();
});
