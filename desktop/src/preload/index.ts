import { createRequire } from 'node:module';
import type {
  PixelPilotApi,
  RendererConfirmationRequest,
  RuntimeEventEnvelope,
  RuntimeSnapshot,
  StartupDefaultsSnapshot,
  SidecarFrame,
  WindowKind
} from '../shared/types.js';
import { unwrapIpcResult, type IpcResult } from '../shared/ipc-result.js';

const require = createRequire(import.meta.url);
const electron = require('electron') as typeof import('electron');
const { contextBridge, ipcRenderer } = electron;

function invokeIpc<T>(channel: string, ...args: unknown[]): Promise<T> {
  return ipcRenderer.invoke(channel, ...args).then((result: IpcResult<T>) => unwrapIpcResult(result));
}

const pixelPilot: PixelPilotApi = {
  getWindowKind: (): Promise<WindowKind> => ipcRenderer.invoke('pixelpilot:get-window-kind'),
  getSnapshot: (): Promise<RuntimeSnapshot | null> => ipcRenderer.invoke('pixelpilot:get-snapshot'),
  getStartupDefaults: (): Promise<StartupDefaultsSnapshot> =>
    invokeIpc<StartupDefaultsSnapshot>('pixelpilot:get-startup-defaults'),
  invokeRuntime: (method: string, payload?: Record<string, unknown>) =>
    invokeIpc<Record<string, unknown>>('pixelpilot:invoke-runtime', method, payload),
  setExpanded: (expanded: boolean) => invokeIpc<Record<string, unknown>>('pixelpilot:set-expanded', expanded),
  setBackgroundHidden: (hidden: boolean) =>
    invokeIpc<Record<string, unknown>>('pixelpilot:set-background-hidden', hidden),
  setTrayOnly: (enabled: boolean) => invokeIpc<Record<string, unknown>>('pixelpilot:set-tray-only', enabled),
  toggleSettingsWindow: () => invokeIpc<{ visible: boolean }>('pixelpilot:toggle-settings-window'),
  closeSettingsWindow: () => invokeIpc<{ visible: boolean }>('pixelpilot:close-settings-window'),
  toggleStartupSettingsWindow: () => invokeIpc<{ visible: boolean }>('pixelpilot:toggle-startup-settings-window'),
  closeStartupSettingsWindow: () => invokeIpc<{ visible: boolean }>('pixelpilot:close-startup-settings-window'),
  setStartupDefaults: (payload) =>
    invokeIpc<StartupDefaultsSnapshot>('pixelpilot:set-startup-defaults', payload),
  updateWindowLayout: (payload) => invokeIpc<void>('pixelpilot:update-window-layout', payload),
  resolveConfirmation: (id: string, payload: Record<string, unknown>) =>
    invokeIpc<Record<string, unknown>>('pixelpilot:resolve-confirmation', id, payload),
  quitApp: () => invokeIpc<void>('pixelpilot:quit-app'),
  onState: (listener: (snapshot: RuntimeSnapshot) => void) => {
    const handler = (_event: unknown, snapshot: RuntimeSnapshot) => listener(snapshot);
    ipcRenderer.on('runtime:state', handler);
    return () => ipcRenderer.removeListener('runtime:state', handler);
  },
  onEvent: (listener: (event: RuntimeEventEnvelope) => void) => {
    const handler = (_event: unknown, envelope: RuntimeEventEnvelope) => listener(envelope);
    ipcRenderer.on('runtime:event', handler);
    return () => ipcRenderer.removeListener('runtime:event', handler);
  },
  onConfirmationRequest: (listener: (request: RendererConfirmationRequest) => void) => {
    const handler = (_event: unknown, request: RendererConfirmationRequest) => listener(request);
    ipcRenderer.on('runtime:confirmation-request', handler);
    return () => ipcRenderer.removeListener('runtime:confirmation-request', handler);
  },
  onSidecarFrame: (listener: (frame: SidecarFrame) => void) => {
    const handler = (_event: unknown, frame: SidecarFrame) => listener(frame);
    ipcRenderer.on('runtime:sidecar-frame', handler);
    return () => ipcRenderer.removeListener('runtime:sidecar-frame', handler);
  }
};

contextBridge.exposeInMainWorld('pixelPilot', pixelPilot);
