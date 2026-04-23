import type { BrowserWindow as BrowserWindowType, Tray as TrayType } from 'electron';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type {
  BridgeStatus,
  RendererConfirmationRequest,
  RuntimeEventEnvelope,
  RuntimeSnapshot,
  SidecarFrame,
  StartupDefaultsSnapshot,
  UiPreferences,
  WindowKind,
  WindowLayoutPayload
} from '../shared/types.js';
import { failureResult, successResult, type IpcResult } from '../shared/ipc-result.js';
import { getAnchoredWindowBounds, normalizeWindowSize } from './window-layout.js';
import { StartupDefaultsStore } from './startup-defaults.js';
import { UiPreferencesStore } from './ui-preferences.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const require = createRequire(import.meta.url);
const electron = require('electron') as typeof import('electron');
const { BrowserWindow, Menu, Tray, app, globalShortcut, ipcMain, nativeImage, screen, shell } = electron;

type InvokeRuntime = (method: string, payload?: Record<string, unknown>) => Promise<Record<string, unknown>>;
type WindowManagerOptions = {
  launchToTray?: boolean;
};
type PendingConfirmation = {
  resolve: (payload: Record<string, unknown>) => void;
  timer: NodeJS.Timeout;
};

const CONFIRMATION_TIMEOUT_MS = Math.max(
  5_000,
  Number(process.env.PIXELPILOT_CONFIRMATION_TIMEOUT_MS || 60_000)
);

function latestActionUpdate(snapshot: RuntimeSnapshot | null): RuntimeSnapshot['recentActionUpdates'][number] | null {
  const updates = snapshot?.recentActionUpdates ?? [];
  for (let index = updates.length - 1; index >= 0; index -= 1) {
    const update = updates[index];
    if (update?.error || update?.message || update?.name || update?.status) {
      return update;
    }
  }
  return null;
}

function isDoneStatus(status: unknown): boolean {
  const normalized = String(status || '').trim().toLowerCase();
  return (
    normalized.includes('done')
    || normalized.includes('complete')
    || normalized.includes('success')
    || normalized.includes('finished')
  );
}

function isErrorStatus(status: unknown): boolean {
  const normalized = String(status || '').trim().toLowerCase();
  return normalized.includes('error') || normalized.includes('fail') || normalized.includes('cancel');
}

function statusSurfaceIsActive(snapshot: RuntimeSnapshot | null): boolean {
  if (!snapshot || snapshot.auth.needsAuth) {
    return false;
  }
  const liveState = String(snapshot.liveSessionState || '').trim().toLowerCase();
  const wakeWordState = String(snapshot.wakeWordState || '').trim().toLowerCase();
  const wakeWordHasTakenOver = liveState === 'disconnected' && wakeWordState === 'armed';
  if (snapshot.bridgeStatus === 'starting' || snapshot.bridgeStatus === 'recovering' || snapshot.bridgeStatus === 'failed') {
    return true;
  }
  const liveStatus = snapshot.liveStatus;
  if (liveStatus && liveStatus.level !== 'idle' && String(liveStatus.message || '').trim()) {
    if (wakeWordHasTakenOver && liveStatus.level === 'info') {
      return false;
    }
    return true;
  }
  if (snapshot.liveVoiceActive) {
    return true;
  }
  if (Number(snapshot.userAudioLevel || 0) > 0.02 || Number(snapshot.assistantAudioLevel || 0) > 0.02) {
    return true;
  }
  if (['connecting', 'listening', 'thinking', 'waiting', 'acting', 'interrupted'].includes(liveState)) {
    return true;
  }
  const latest = latestActionUpdate(snapshot);
  if (!latest) {
    return false;
  }
  if (latest.error || isErrorStatus(latest.status)) {
    return true;
  }
  return latest.done !== true && !isDoneStatus(latest.status);
}

function isPersistentLiveStatus(snapshot: RuntimeSnapshot | null): boolean {
  if (!snapshot || snapshot.auth.needsAuth) {
    return false;
  }
  const liveStatus = snapshot.liveStatus;
  if (!liveStatus || liveStatus.level === 'idle') {
    return false;
  }
  const code = String(liveStatus.code || '').trim().toLowerCase();
  const message = String(liveStatus.message || '').trim().toLowerCase();
  return (
    code.includes('rate_limited')
    || code.includes('rate-limit')
    || message.includes('rate limit')
    || message.includes('daily time limit exceeded')
  );
}

function actionUpdateLabel(snapshot: RuntimeSnapshot | null): string {
  const latest = latestActionUpdate(snapshot);
  if (!latest) {
    return '';
  }
  const error = String(latest.error || '').trim();
  const message = String(latest.message || '').trim();
  const name = String(latest.name || '').trim();
  const statusText = String(latest.status || '').trim();
  if (message) {
    return message;
  }
  if (error && error !== statusText) {
    return error;
  }
  if (name && statusText) {
    return `${name}: ${statusText}`;
  }
  return name || statusText;
}

function latestAssistantText(snapshot: RuntimeSnapshot | null): string {
  const messages = snapshot?.recentMessages ?? [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const entry = messages[index];
    const kind = String(entry?.kind || '').trim().toLowerCase();
    const speaker = String(entry?.speaker || '').trim().toLowerCase();
    const text = String(entry?.text || '').trim();
    if (text && (kind === 'assistant' || speaker === 'assistant')) {
      return text;
    }
  }
  return '';
}

function notchSurfaceText(snapshot: RuntimeSnapshot | null): string {
  if (!snapshot) {
    return '';
  }
  const liveStatus = snapshot.liveStatus;
  if (liveStatus && liveStatus.level !== 'idle' && String(liveStatus.message || '').trim()) {
    return String(liveStatus.message).trim();
  }
  const actionLabel = actionUpdateLabel(snapshot);
  if (actionLabel) {
    return actionLabel;
  }
  return latestAssistantText(snapshot);
}

function trayStatusLabel(snapshot: RuntimeSnapshot | null, bridgeStatusMessage: string): string {
  if (!snapshot) {
    return bridgeStatusMessage || 'Starting runtime...';
  }
  const liveStatus = snapshot.liveStatus;
  if (liveStatus && liveStatus.level !== 'idle' && String(liveStatus.message || '').trim()) {
    return String(liveStatus.message).trim();
  }
  const actionLabel = actionUpdateLabel(snapshot);
  if (actionLabel && statusSurfaceIsActive(snapshot)) {
    return actionLabel;
  }
  const liveState = String(snapshot.liveSessionState || '').trim();
  if (liveState) {
    return `Live: ${liveState}`;
  }
  return bridgeStatusMessage || 'Ready';
}

export class WindowManager {
  private overlayWindow: BrowserWindowType | null = null;
  private notchWindow: BrowserWindowType | null = null;
  private glowWindow: BrowserWindowType | null = null;
  private sidecarWindow: BrowserWindowType | null = null;
  private settingsWindow: BrowserWindowType | null = null;
  private tray: TrayType | null = null;
  private readonly invokeRuntime: InvokeRuntime;
  private readonly startupDefaultsStore: StartupDefaultsStore;
  private readonly uiPreferencesStore: UiPreferencesStore;
  private readonly kinds = new Map<number, WindowKind>();
  private currentSnapshot: RuntimeSnapshot | null = null;
  private uiPreferences: UiPreferences;
  private bridgeStatus: BridgeStatus = 'starting';
  private bridgeStatusMessage = 'Starting runtime...';
  private pendingConfirmations = new Map<string, PendingConfirmation>();
  private runtimeClickThrough = false;
  private manualClickThroughOverride: boolean | null = null;
  private trayOnly = false;
  private readonly userMovedKinds = new Set<WindowKind>();
  private readonly programmaticMoveKinds = new Set<WindowKind>();
  private launchToTrayPending: boolean;
  private readonly showManualLaunchGlow: boolean;
  private initialPostLoginHideApplied = false;
  private previousWakeWordState = '';
  private previousLiveVoiceActive = false;
  private wakeStateInitialized = false;
  private wakeNotchActive = false;
  private notchTextInitialized = false;
  private previousNotchSurfaceText = '';
  private notchVisibleUntil = 0;
  private notchVisibilityTimer: NodeJS.Timeout | null = null;
  private transientGlowUntil = 0;
  private transientGlowTimer: NodeJS.Timeout | null = null;

  constructor(
    invokeRuntime: InvokeRuntime,
    startupDefaultsStore: StartupDefaultsStore,
    uiPreferencesStore: UiPreferencesStore,
    options: WindowManagerOptions = {},
  ) {
    this.invokeRuntime = invokeRuntime;
    this.startupDefaultsStore = startupDefaultsStore;
    this.uiPreferencesStore = uiPreferencesStore;
    this.uiPreferences = uiPreferencesStore.load();
    this.showManualLaunchGlow = !Boolean(options.launchToTray);
    this.launchToTrayPending = Boolean(options.launchToTray);
    this.registerIpc();
  }

  createWindows(): void {
    this.overlayWindow = this.createWindow('overlay', 920, 180, false);
    this.notchWindow = this.createWindow('notch', 420, 76, true);
    this.glowWindow = this.createWindow('glow', 400, 300, true);
    this.sidecarWindow = this.createWindow('sidecar', 390, 420, true);
    this.settingsWindow = this.createWindow('settings', 760, 620, true);
    this.notchWindow.hide();
    this.glowWindow.hide();
    this.sidecarWindow.hide();
    this.settingsWindow.hide();
    this.repositionAnchoredWindows();
    screen.on('display-metrics-changed', this.repositionAnchoredWindows);
    screen.on('display-added', this.repositionAnchoredWindows);
    screen.on('display-removed', this.repositionAnchoredWindows);
    this.createTray();
    this.registerShortcuts();
  }

  dispose(): void {
    globalShortcut.unregisterAll();
    if (this.transientGlowTimer) {
      clearTimeout(this.transientGlowTimer);
      this.transientGlowTimer = null;
    }
    if (this.notchVisibilityTimer) {
      clearTimeout(this.notchVisibilityTimer);
      this.notchVisibilityTimer = null;
    }
    for (const pending of this.pendingConfirmations.values()) {
      clearTimeout(pending.timer);
      pending.resolve({ approved: false, error: 'window_manager_disposed' });
    }
    this.pendingConfirmations.clear();
    screen.removeListener('display-metrics-changed', this.repositionAnchoredWindows);
    screen.removeListener('display-added', this.repositionAnchoredWindows);
    screen.removeListener('display-removed', this.repositionAnchoredWindows);
    this.tray?.destroy();
    this.tray = null;
    this.overlayWindow?.destroy();
    this.notchWindow?.destroy();
    this.glowWindow?.destroy();
    this.sidecarWindow?.destroy();
    this.settingsWindow?.destroy();
  }

  getWindowKind(senderId: number): WindowKind {
    return this.kinds.get(senderId) ?? 'overlay';
  }

  applySnapshot(snapshot: RuntimeSnapshot): void {
    this.currentSnapshot = this.decorateSnapshot(snapshot);
    this.updateTransientWakeGlow(this.currentSnapshot);
    this.updateNotchVisibilityTail(this.currentSnapshot);
    if (this.currentSnapshot.auth.needsAuth) {
      this.initialPostLoginHideApplied = false;
    }
    if (!this.currentSnapshot.auth.needsAuth && !this.initialPostLoginHideApplied) {
      this.initialPostLoginHideApplied = true;
      if (this.showManualLaunchGlow) {
        this.startTransientGlow(1800);
      }
      if (!this.currentSnapshot.backgroundHidden) {
        this.fireAndForget(this.setBackgroundHidden(true));
        return;
      }
    }
    if (!snapshot.backgroundHidden || snapshot.auth.needsAuth) {
      if (!this.launchToTrayPending) {
        this.trayOnly = false;
      }
    }
    this.runtimeClickThrough = snapshot.auth.needsAuth ? false : snapshot.clickThroughEnabled;
    this.broadcastState(this.currentSnapshot);
    this.syncWindowVisibility();
    this.applyClickThrough();
    this.rebuildTrayMenu();
  }

  setBridgeState(status: BridgeStatus, message = ''): void {
    this.bridgeStatus = status;
    this.bridgeStatusMessage = message;
    if (!this.currentSnapshot) {
      return;
    }
    this.currentSnapshot = this.decorateSnapshot(this.currentSnapshot);
    this.broadcastState(this.currentSnapshot);
    this.syncWindowVisibility();
    this.rebuildTrayMenu();
  }

  broadcastEvent(envelope: RuntimeEventEnvelope): void {
    for (const win of [
      this.overlayWindow,
      this.notchWindow,
      this.glowWindow,
      this.sidecarWindow,
      this.settingsWindow,
    ]) {
      win?.webContents.send('runtime:event', envelope);
    }
  }

  sendSidecarFrame(frame: SidecarFrame): void {
    this.sidecarWindow?.webContents.send('runtime:sidecar-frame', frame);
  }

  async handleRuntimeRequest(envelope: RuntimeEventEnvelope): Promise<Record<string, unknown>> {
    if (envelope.method === 'ui.requestConfirmation') {
      return this.promptConfirmation({
        id: envelope.id,
        title: String(envelope.payload.title || 'Confirm'),
        text: String(envelope.payload.text || '')
      });
    }
    if (envelope.method === 'ui.prepareForScreenshot') {
      return this.prepareForScreenshot();
    }
    if (envelope.method === 'ui.restoreAfterScreenshot') {
      this.restoreAfterScreenshot(envelope.payload as Record<string, unknown>);
      return { restored: true };
    }
    if (envelope.method === 'shell.setClickThrough') {
      this.runtimeClickThrough = Boolean(envelope.payload.enabled);
      this.applyClickThrough();
      return { enabled: this.runtimeClickThrough };
    }
    return {};
  }

  private createWindow(kind: WindowKind, width: number, height: number, skipTaskbar: boolean): BrowserWindowType {
    const isGlow = kind === 'glow';
    const isNotch = kind === 'notch';
    const isClickThroughSurface = isGlow || isNotch;
    const window = new BrowserWindow({
      width,
      height,
      icon: this.resolveIconPath(),
      frame: false,
      show: false,
      transparent: true,
      resizable: kind === 'settings',
      hasShadow: false,
      skipTaskbar,
      alwaysOnTop: kind !== 'overlay',
      movable:
        !isClickThroughSurface
        && kind !== 'settings',
      maximizable: false,
      minimizable: false,
      backgroundColor: '#00000000',
      focusable: !isClickThroughSurface,
      webPreferences: {
        preload: path.join(__dirname, '../preload/index.js'),
        contextIsolation: true,
        sandbox: false
      }
    });
    if (kind === 'settings') {
      this.applySettingsWindowSizeLimits(window, screen.getPrimaryDisplay().workArea);
    }
    if (isClickThroughSurface) {
      window.setIgnoreMouseEvents(true, { forward: true });
      this.elevateStatusSurface(window);
    }
    this.kinds.set(window.webContents.id, kind);
    window.on('moved', () => {
      if (!this.programmaticMoveKinds.has(kind) && kind !== 'notch') {
        this.userMovedKinds.add(kind);
      }
      if (
        kind === 'overlay'
        && this.settingsWindow?.isVisible()
      ) {
        this.positionSettingsWindow();
      }
    });
    void this.loadRenderer(window, kind);
    return window;
  }

  private async loadRenderer(window: BrowserWindowType, kind: WindowKind): Promise<void> {
    const devUrl = process.env.PIXELPILOT_RENDERER_URL;
    if (devUrl) {
      await window.loadURL(`${devUrl}?window=${kind}`);
    } else {
      await window.loadFile(path.join(__dirname, '../renderer/index.html'), { query: { window: kind } });
    }
  }

  private createTray(): void {
    const iconPath = this.resolveIconPath();
    const icon = iconPath ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
    this.tray = new Tray(icon);
    this.tray.setToolTip('PixelPilot');
    this.rebuildTrayMenu();
    this.tray.on('click', () => {
      this.fireAndForget(this.setBackgroundHidden(false));
    });
  }

  private rebuildTrayMenu(): void {
    if (!this.tray) {
      return;
    }

    const snapshot = this.currentSnapshot;
    const authRequired = Boolean(snapshot?.auth.needsAuth);
    const bridgeBusy = this.bridgeStatus === 'starting' || this.bridgeStatus === 'recovering';
    const liveAvailable = Boolean(snapshot?.liveAvailable);
    const liveVoiceActive = Boolean(snapshot?.liveVoiceActive);
    const wakeWordEnabled = Boolean(snapshot?.wakeWordEnabled);
    const liveState = String(snapshot?.liveSessionState || '').trim().toLowerCase();
    const agentPreviewAvailable = Boolean(snapshot?.agentViewEnabled);
    const agentPreviewRequested = Boolean(snapshot?.agentViewRequested);

    this.tray.setToolTip(`PixelPilot - ${trayStatusLabel(snapshot, this.bridgeStatusMessage)}`);
    this.tray.setContextMenu(
      Menu.buildFromTemplate([
        {
          label: authRequired ? 'Open PixelPilot Login' : 'Open Command Bar',
          click: () => this.fireAndForget(this.setBackgroundHidden(false)),
        },
        {
          label: 'Stop Current Task',
          enabled: !authRequired && !bridgeBusy,
          click: () => this.fireAndForget(this.invokeRuntime('live.stop')),
        },
        { type: 'separator' },
        {
          label: 'Mode',
          enabled: !authRequired && !bridgeBusy,
          submenu: [
            {
              label: 'Guidance',
              type: 'radio',
              checked: snapshot?.operationMode === 'GUIDANCE',
              click: () => this.fireAndForget(this.invokeRuntime('mode.set', { value: 'GUIDANCE' })),
            },
            {
              label: 'Safe',
              type: 'radio',
              checked: snapshot?.operationMode === 'SAFE',
              click: () => this.fireAndForget(this.invokeRuntime('mode.set', { value: 'SAFE' })),
            },
            {
              label: 'Auto',
              type: 'radio',
              checked: snapshot?.operationMode === 'AUTO',
              click: () => this.fireAndForget(this.invokeRuntime('mode.set', { value: 'AUTO' })),
            },
          ],
        },
        {
          label: 'Input',
          enabled: !authRequired && !bridgeBusy,
          submenu: [
            {
              label: liveVoiceActive ? 'Stop Voice Input' : 'Start Voice Input',
              enabled: liveAvailable,
              click: () => this.fireAndForget(this.invokeRuntime('live.setVoice', { enabled: !liveVoiceActive })),
            },
            {
              label: wakeWordEnabled ? 'Disable Wake Word' : 'Enable Wake Word',
              click: () => this.fireAndForget(this.invokeRuntime('wakeWord.setEnabled', { enabled: !wakeWordEnabled })),
            },
            {
              label: liveState === 'disconnected' ? 'Reconnect Live' : 'Disconnect Live',
              enabled: liveAvailable,
              click: () => this.fireAndForget(this.invokeRuntime('live.setEnabled', { enabled: liveState === 'disconnected' })),
            },
          ],
        },
        {
          label: 'Visibility',
          enabled: !authRequired,
          submenu: [
            {
              label: 'Corner Glow',
              type: 'checkbox',
              checked: this.uiPreferences.cornerGlowEnabled,
              click: () => this.setUiPreferences({ cornerGlowEnabled: !this.uiPreferences.cornerGlowEnabled }),
            },
            {
              label: 'Status Notch',
              type: 'checkbox',
              checked: this.uiPreferences.statusNotchEnabled,
              click: () => this.setUiPreferences({ statusNotchEnabled: !this.uiPreferences.statusNotchEnabled }),
            },
            {
              label: 'Agent Preview',
              type: 'checkbox',
              enabled: agentPreviewAvailable,
              checked: agentPreviewRequested,
              click: () => this.fireAndForget(this.invokeRuntime('agentView.setRequested', { requested: !agentPreviewRequested })),
            },
          ],
        },
        { type: 'separator' },
        {
          label: 'Settings',
          enabled: !authRequired,
          click: () => {
            this.openSettingsWindow();
          },
        },
        {
          label: 'Run Diagnostics',
          enabled: !authRequired && !bridgeBusy,
          click: () => this.fireAndForget(this.invokeRuntime('doctor.run')),
        },
        {
          label: 'Open Logs',
          click: () => this.fireAndForget(this.openLogsFolder()),
        },
        { type: 'separator' },
        { label: 'Quit PixelPilot', click: () => app.quit() },
      ])
    );
  }

  private resolveIconPath(): string | undefined {
    const candidates = app.isPackaged
      ? [path.join(process.resourcesPath, 'tray-icon.ico')]
      : [
          path.join(app.getAppPath(), 'build', 'icon.ico'),
          path.join(__dirname, '../../build/icon.ico'),
          path.join(__dirname, '../../..', 'src', 'logos', 'pixelpilot-icon.ico'),
        ];

    for (const candidate of candidates) {
      try {
        if (fs.existsSync(candidate)) {
          return candidate;
        }
      } catch {
        // Keep trying fallbacks.
      }
    }

    return undefined;
  }

  private registerShortcuts(): void {
    globalShortcut.register('CommandOrControl+Shift+Q', () => app.quit());
    globalShortcut.register('CommandOrControl+Shift+Space', () => {
      this.fireAndForget(this.setBackgroundHidden(false));
    });
    globalShortcut.register('CommandOrControl+Shift+X', () => {
      this.fireAndForget(this.invokeRuntime('live.stop'));
    });
    globalShortcut.register('CommandOrControl+Shift+M', () => {
      this.fireAndForget(this.toggleBackground());
    });
    globalShortcut.register('CommandOrControl+Shift+D', () => {
      this.fireAndForget(this.toggleExpanded());
    });
    globalShortcut.register('CommandOrControl+Shift+Z', () => {
      this.toggleManualClickThrough();
    });
  }

  private registerIpc(): void {
    ipcMain.handle('pixelpilot:get-window-kind', (event) => this.getWindowKind(event.sender.id));
    ipcMain.handle('pixelpilot:get-snapshot', () => this.currentSnapshot);
    ipcMain.handle('pixelpilot:get-ui-preferences', () =>
      this.wrapIpcResult(() => this.getUiPreferences())
    );
    ipcMain.handle('pixelpilot:set-ui-preferences', (_event, payload: Partial<UiPreferences>) =>
      this.wrapIpcResult(() => this.setUiPreferences(payload))
    );
    ipcMain.handle('pixelpilot:get-startup-defaults', () =>
      this.wrapIpcResult(() => this.getStartupDefaults())
    );
    ipcMain.handle('pixelpilot:invoke-runtime', (_event, method: string, payload?: Record<string, unknown>) =>
      this.wrapIpcResult(() => this.invokeRuntime(method, payload))
    );
    ipcMain.handle('pixelpilot:open-external', (_event, url: string) =>
      this.wrapIpcResult(async () => {
        await shell.openExternal(String(url || ''));
      })
    );
    ipcMain.handle('pixelpilot:set-expanded', (_event, expanded: boolean) =>
      this.wrapIpcResult(() => this.setExpanded(Boolean(expanded)))
    );
    ipcMain.handle('pixelpilot:set-background-hidden', (_event, hidden: boolean) =>
      this.wrapIpcResult(() => this.setBackgroundHidden(Boolean(hidden)))
    );
    ipcMain.handle('pixelpilot:set-tray-only', (_event, enabled: boolean) =>
      this.wrapIpcResult(() => this.setTrayOnly(Boolean(enabled)))
    );
    ipcMain.handle('pixelpilot:toggle-settings-window', () =>
      this.wrapIpcResult(() => this.toggleSettingsWindow())
    );
    ipcMain.handle('pixelpilot:close-settings-window', () =>
      this.wrapIpcResult(() => this.closeSettingsWindow())
    );
    ipcMain.handle(
      'pixelpilot:set-startup-defaults',
      (_event, payload: Record<string, unknown>) =>
        this.wrapIpcResult(() => this.setStartupDefaults(payload))
    );
    ipcMain.handle('pixelpilot:update-window-layout', (event, payload: WindowLayoutPayload) =>
      this.wrapIpcResult(() => this.updateWindowLayout(event.sender.id, payload))
    );
    ipcMain.handle('pixelpilot:resolve-confirmation', (_event, id: string, payload: Record<string, unknown>) =>
      this.wrapIpcResult(() => {
        const resolver = this.pendingConfirmations.get(id);
        if (resolver) {
          this.pendingConfirmations.delete(id);
          clearTimeout(resolver.timer);
          resolver.resolve(payload);
          this.syncWindowVisibility();
        }
        return { ok: true };
      })
    );
    ipcMain.handle('pixelpilot:quit-app', () =>
      this.wrapIpcResult(() => {
        app.quit();
      })
    );
  }

  private syncWindowVisibility(): void {
    if (!this.currentSnapshot) {
      return;
    }
    if (this.currentSnapshot.auth.needsAuth) {
      this.overlayWindow?.setAlwaysOnTop(true, 'floating');
      this.overlayWindow?.show();
      this.notchWindow?.hide();
      this.glowWindow?.hide();
      this.sidecarWindow?.hide();
      this.settingsWindow?.hide();
      return;
    }

    if (this.launchToTrayPending) {
      this.overlayWindow?.hide();
      this.notchWindow?.hide();
      this.glowWindow?.hide();
      this.sidecarWindow?.hide();
      this.settingsWindow?.hide();
      return;
    }

    const activeStatus = statusSurfaceIsActive(this.currentSnapshot) || this.hasTransientGlow();
    const commandHidden = Boolean(this.currentSnapshot.backgroundHidden) && this.pendingConfirmations.size === 0;
    const notchShouldShow =
      this.uiPreferences.statusNotchEnabled
      && !this.trayOnly
      && this.notchSurfaceIsVisible(this.currentSnapshot);
    if (commandHidden) {
      this.overlayWindow?.hide();
      if (notchShouldShow) {
        this.anchorWindow('notch');
        this.ensureStatusSurfaceVisible(this.notchWindow);
      } else {
        this.notchWindow?.hide();
      }
    } else {
      this.anchorWindow('overlay');
      this.overlayWindow?.setAlwaysOnTop(false);
      this.overlayWindow?.show();
      if (notchShouldShow) {
        this.anchorWindow('notch');
        this.ensureStatusSurfaceVisible(this.notchWindow);
      } else {
        this.notchWindow?.hide();
      }
    }

    if (activeStatus && this.uiPreferences.cornerGlowEnabled) {
      this.anchorWindow('glow');
      this.ensureStatusSurfaceVisible(this.glowWindow);
    } else {
      this.glowWindow?.hide();
    }

    if (this.currentSnapshot.sidecarVisible && !this.currentSnapshot.auth.needsAuth) {
      this.sidecarWindow?.showInactive();
    } else {
      this.sidecarWindow?.hide();
    }
  }

  private applyClickThrough(): void {
    const effective =
      this.manualClickThroughOverride === null ? this.runtimeClickThrough : this.manualClickThroughOverride;
    this.overlayWindow?.setIgnoreMouseEvents(effective, { forward: true });
  }

  private toggleManualClickThrough(): void {
    const effective =
      this.manualClickThroughOverride === null ? this.runtimeClickThrough : this.manualClickThroughOverride;
    const next = !effective;
    this.manualClickThroughOverride = next === this.runtimeClickThrough ? null : next;
    this.applyClickThrough();
  }

  private broadcastState(snapshot: RuntimeSnapshot): void {
    for (const win of [
      this.overlayWindow,
      this.notchWindow,
      this.glowWindow,
      this.sidecarWindow,
      this.settingsWindow,
    ]) {
      win?.webContents.send('runtime:state', snapshot);
    }
  }

  private decorateSnapshot(snapshot: RuntimeSnapshot): RuntimeSnapshot {
    return {
      ...snapshot,
      bridgeStatus: this.bridgeStatus,
      bridgeStatusMessage: this.bridgeStatusMessage,
      uiPreferences: { ...this.uiPreferences },
    };
  }

  private getUiPreferences(): UiPreferences {
    this.uiPreferences = this.uiPreferencesStore.load();
    return { ...this.uiPreferences };
  }

  private setUiPreferences(payload: Partial<UiPreferences>): UiPreferences {
    this.uiPreferences = this.uiPreferencesStore.save(payload);
    if (!this.uiPreferences.cornerGlowEnabled) {
      this.clearTransientGlow();
    }
    if (this.currentSnapshot) {
      this.currentSnapshot = this.decorateSnapshot(this.currentSnapshot);
      this.broadcastState(this.currentSnapshot);
      this.syncWindowVisibility();
    }
    this.rebuildTrayMenu();
    return { ...this.uiPreferences };
  }

  private getStartupDefaults(): StartupDefaultsSnapshot {
    return this.startupDefaultsStore.resolve(this.currentSnapshot);
  }

  private setStartupDefaults(payload: Record<string, unknown>): StartupDefaultsSnapshot {
    const operationMode = String(payload.operationMode || '').trim().toUpperCase();
    const visionMode = String(payload.visionMode || '').trim().toUpperCase();
    return this.startupDefaultsStore.save({
      operationMode: operationMode as StartupDefaultsSnapshot['operationMode'],
      visionMode: visionMode as StartupDefaultsSnapshot['visionMode'],
    });
  }

  private async promptConfirmation(request: RendererConfirmationRequest): Promise<Record<string, unknown>> {
    if (!this.overlayWindow) {
      return { approved: false };
    }
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        const pending = this.pendingConfirmations.get(request.id);
        if (!pending) {
          return;
        }
        this.pendingConfirmations.delete(request.id);
        pending.resolve({
          approved: false,
          error: 'confirmation_timeout',
          message: 'Confirmation timed out.',
        });
        this.syncWindowVisibility();
      }, CONFIRMATION_TIMEOUT_MS);
      this.pendingConfirmations.set(request.id, { resolve, timer });
      this.syncWindowVisibility();
      this.overlayWindow?.show();
      this.overlayWindow?.webContents.send('runtime:confirmation-request', request);
    });
  }

  private prepareForScreenshot(): Record<string, unknown> {
    const payload = {
      overlay_visible: this.isWindowVisible(this.overlayWindow),
      notch_visible: this.isWindowVisible(this.notchWindow),
      glow_visible: this.isWindowVisible(this.glowWindow),
      sidecar_visible: this.isWindowVisible(this.sidecarWindow),
      settings_visible: this.isWindowVisible(this.settingsWindow),
      restore_main_window: Boolean(this.currentSnapshot && !this.currentSnapshot.backgroundHidden),
      restore_minimized_notch: Boolean(this.currentSnapshot?.backgroundHidden && !this.trayOnly),
      restore_tray_only: Boolean(this.currentSnapshot?.backgroundHidden && this.trayOnly),
    };
    this.overlayWindow?.hide();
    this.glowWindow?.hide();
    this.sidecarWindow?.hide();
    this.settingsWindow?.hide();
    return payload;
  }

  private restoreAfterScreenshot(payload: Record<string, unknown>): void {
    const hasExactVisibilityState =
      Object.prototype.hasOwnProperty.call(payload, 'overlay_visible')
      || Object.prototype.hasOwnProperty.call(payload, 'notch_visible')
      || Object.prototype.hasOwnProperty.call(payload, 'glow_visible')
      || Object.prototype.hasOwnProperty.call(payload, 'sidecar_visible')
      || Object.prototype.hasOwnProperty.call(payload, 'settings_visible');

    if (hasExactVisibilityState) {
      this.restoreWindowVisibility(this.overlayWindow, Boolean(payload.overlay_visible));
      this.restoreWindowVisibility(this.notchWindow, Boolean(payload.notch_visible));
      this.restoreWindowVisibility(this.glowWindow, Boolean(payload.glow_visible));
      this.restoreWindowVisibility(this.sidecarWindow, Boolean(payload.sidecar_visible));
      if (Boolean(payload.settings_visible)) {
        this.positionSettingsWindow();
      }
      this.restoreWindowVisibility(this.settingsWindow, Boolean(payload.settings_visible));
      return;
    }

    if (Boolean(payload.restore_tray_only)) {
      this.restoreWindowVisibility(this.overlayWindow, false);
      this.restoreWindowVisibility(this.notchWindow, false);
    } else if (Boolean(payload.restore_main_window)) {
      this.restoreWindowVisibility(this.overlayWindow, true);
      this.restoreWindowVisibility(this.notchWindow, false);
    } else if (Boolean(payload.restore_minimized_notch)) {
      this.restoreWindowVisibility(this.notchWindow, true);
      this.restoreWindowVisibility(this.overlayWindow, false);
    }
    this.restoreWindowVisibility(this.glowWindow, Boolean(statusSurfaceIsActive(this.currentSnapshot) && this.uiPreferences.cornerGlowEnabled));
    this.restoreWindowVisibility(this.sidecarWindow, Boolean(this.currentSnapshot?.sidecarVisible));
    this.restoreWindowVisibility(this.settingsWindow, false);
  }

  private isWindowVisible(window: BrowserWindowType | null): boolean {
    return Boolean(window && !window.isDestroyed() && window.isVisible());
  }

  private restoreWindowVisibility(window: BrowserWindowType | null, visible: boolean): void {
    if (!window || window.isDestroyed()) {
      return;
    }
    if (visible) {
      const kind = this.kinds.get(window.webContents.id);
      if (kind === 'notch' || kind === 'glow') {
        this.elevateStatusSurface(window);
      }
      window.showInactive();
      return;
    }
    window.hide();
  }

  private elevateStatusSurface(window: BrowserWindowType | null): void {
    if (!window || window.isDestroyed()) {
      return;
    }
    window.setAlwaysOnTop(true, 'screen-saver', 1);
    window.moveTop();
  }

  private ensureStatusSurfaceVisible(window: BrowserWindowType | null): void {
    if (!window || window.isDestroyed()) {
      return;
    }
    if (window.isVisible()) {
      return;
    }
    this.elevateStatusSurface(window);
    window.showInactive();
  }

  private async toggleBackground(): Promise<void> {
    const next = !Boolean(this.currentSnapshot?.backgroundHidden);
    await this.setBackgroundHidden(next);
  }

  private async toggleExpanded(): Promise<void> {
    const next = !Boolean(this.currentSnapshot?.expanded);
    await this.setExpanded(next);
  }

  private async setExpanded(expanded: boolean): Promise<Record<string, unknown>> {
    const previousSnapshot = this.currentSnapshot ? { ...this.currentSnapshot } : null;
    if (this.currentSnapshot) {
      this.currentSnapshot = { ...this.currentSnapshot, expanded };
      this.broadcastState(this.currentSnapshot);
    }
    try {
      return await this.invokeRuntime('shell.setExpanded', { expanded });
    } catch (error) {
      if (previousSnapshot) {
        this.currentSnapshot = previousSnapshot;
        this.broadcastState(previousSnapshot);
      }
      throw error;
    }
  }

  private async setBackgroundHidden(hidden: boolean): Promise<Record<string, unknown>> {
    const previousSnapshot = this.currentSnapshot ? { ...this.currentSnapshot } : null;
    const previousTrayOnly = this.trayOnly;
    const previousLaunchToTrayPending = this.launchToTrayPending;
    if (!hidden) {
      this.launchToTrayPending = false;
    }
    this.trayOnly = false;
    if (this.currentSnapshot) {
      this.currentSnapshot = { ...this.currentSnapshot, backgroundHidden: hidden };
      this.syncWindowVisibility();
      this.broadcastState(this.currentSnapshot);
      this.rebuildTrayMenu();
    }
    try {
      return await this.invokeRuntime('shell.setBackgroundHidden', { hidden });
    } catch (error) {
      this.launchToTrayPending = previousLaunchToTrayPending;
      this.trayOnly = previousTrayOnly;
      if (previousSnapshot) {
        this.currentSnapshot = previousSnapshot;
        this.syncWindowVisibility();
        this.broadcastState(previousSnapshot);
        this.rebuildTrayMenu();
      }
      throw error;
    }
  }

  private async setTrayOnly(enabled: boolean): Promise<Record<string, unknown>> {
    const previousSnapshot = this.currentSnapshot ? { ...this.currentSnapshot } : null;
    const previousTrayOnly = this.trayOnly;
    const previousLaunchToTrayPending = this.launchToTrayPending;
    const hidden = enabled;

    this.launchToTrayPending = false;
    this.trayOnly = enabled;
    if (this.currentSnapshot) {
      this.currentSnapshot = { ...this.currentSnapshot, backgroundHidden: hidden };
      this.syncWindowVisibility();
      this.broadcastState(this.currentSnapshot);
      this.rebuildTrayMenu();
    }

    try {
      return await this.invokeRuntime('shell.setBackgroundHidden', { hidden });
    } catch (error) {
      this.launchToTrayPending = previousLaunchToTrayPending;
      this.trayOnly = previousTrayOnly;
      if (previousSnapshot) {
        this.currentSnapshot = previousSnapshot;
        this.syncWindowVisibility();
        this.broadcastState(previousSnapshot);
        this.rebuildTrayMenu();
      }
      throw error;
    }
  }

  private hasTransientGlow(): boolean {
    return Date.now() < this.transientGlowUntil;
  }

  private hasNotchVisibilityTail(): boolean {
    return Date.now() < this.notchVisibleUntil;
  }

  private notchSurfaceIsVisible(snapshot: RuntimeSnapshot | null): boolean {
    if (!snapshot || snapshot.auth.needsAuth) {
      return false;
    }
    const liveState = String(snapshot.liveSessionState || '').trim().toLowerCase();
    const wakeWordState = String(snapshot.wakeWordState || '').trim().toLowerCase();
    if (liveState === 'disconnected' && wakeWordState === 'armed' && !isPersistentLiveStatus(snapshot)) {
      return false;
    }
    return statusSurfaceIsActive(snapshot) || this.wakeNotchActive || this.hasNotchVisibilityTail();
  }

  private updateNotchVisibilityTail(snapshot: RuntimeSnapshot): void {
    if (snapshot.auth.needsAuth) {
      this.notchVisibleUntil = 0;
      this.previousNotchSurfaceText = '';
      this.notchTextInitialized = false;
      return;
    }
    const liveState = String(snapshot.liveSessionState || '').trim().toLowerCase();
    const wakeWordState = String(snapshot.wakeWordState || '').trim().toLowerCase();
    if (liveState === 'disconnected' && wakeWordState === 'armed' && !isPersistentLiveStatus(snapshot)) {
      this.notchVisibleUntil = 0;
      this.scheduleNotchVisibilityTimer();
    }

    const text = notchSurfaceText(snapshot);
    if (!this.notchTextInitialized) {
      this.previousNotchSurfaceText = text;
      this.notchTextInitialized = true;
      return;
    }
    if (text && text !== this.previousNotchSurfaceText) {
      this.previousNotchSurfaceText = text;
      this.notchVisibleUntil = Math.max(this.notchVisibleUntil, Date.now() + 8000);
      this.scheduleNotchVisibilityTimer();
      return;
    }
    if (statusSurfaceIsActive(snapshot)) {
      this.notchVisibleUntil = Math.max(this.notchVisibleUntil, Date.now() + 1600);
      this.scheduleNotchVisibilityTimer();
    }
  }

  private scheduleNotchVisibilityTimer(): void {
    if (this.notchVisibilityTimer) {
      clearTimeout(this.notchVisibilityTimer);
      this.notchVisibilityTimer = null;
    }
    if (!this.hasNotchVisibilityTail()) {
      this.syncWindowVisibility();
      return;
    }
    this.notchVisibilityTimer = setTimeout(() => {
      this.notchVisibilityTimer = null;
      this.syncWindowVisibility();
    }, Math.max(250, this.notchVisibleUntil - Date.now() + 60));
  }

  private updateTransientWakeGlow(snapshot: RuntimeSnapshot): void {
    const wakeWordState = String(snapshot.wakeWordState || '').trim().toLowerCase();
    const liveState = String(snapshot.liveSessionState || '').trim().toLowerCase();
    const liveVoiceActive = Boolean(snapshot.liveVoiceActive);
    if (snapshot.auth.needsAuth) {
      this.wakeNotchActive = false;
      this.wakeStateInitialized = false;
      this.previousWakeWordState = wakeWordState;
      this.previousLiveVoiceActive = liveVoiceActive;
      this.clearTransientGlow();
      return;
    }

    if (!this.wakeStateInitialized) {
      this.wakeStateInitialized = true;
      this.wakeNotchActive = false;
      this.previousWakeWordState = wakeWordState;
      this.previousLiveVoiceActive = liveVoiceActive;
      if (wakeWordState === 'armed' && liveState === 'disconnected' && !liveVoiceActive) {
        this.clearTransientGlow();
      }
      return;
    }

    if (
      wakeWordState === 'armed'
      && liveState === 'disconnected'
      && !liveVoiceActive
    ) {
      this.wakeNotchActive = false;
      this.clearTransientGlow();
    } else if (
      (wakeWordState === 'paused' && this.previousWakeWordState === 'armed')
      || (liveVoiceActive && !this.previousLiveVoiceActive && this.previousWakeWordState === 'armed')
      || (wakeWordState === 'paused' && liveVoiceActive)
    ) {
      this.wakeNotchActive = true;
    } else if (['disabled', 'unavailable'].includes(wakeWordState)) {
      this.wakeNotchActive = false;
    }

    if (!this.uiPreferences.cornerGlowEnabled) {
      this.previousWakeWordState = wakeWordState;
      this.previousLiveVoiceActive = liveVoiceActive;
      return;
    }

    if (
      wakeWordState === 'armed'
      && this.previousWakeWordState === 'paused'
      && liveState === 'disconnected'
    ) {
      this.clearTransientGlow();
    } else if (
      wakeWordState === 'armed'
      && this.previousWakeWordState !== 'armed'
      && this.previousWakeWordState !== 'paused'
    ) {
      this.startTransientGlow(1600);
    } else if (
      (wakeWordState === 'paused' && this.previousWakeWordState === 'armed')
      || (liveVoiceActive && !this.previousLiveVoiceActive && this.previousWakeWordState === 'armed')
    ) {
      this.startTransientGlow(2200);
    }

    this.previousWakeWordState = wakeWordState;
    this.previousLiveVoiceActive = liveVoiceActive;
  }

  private clearTransientGlow(): void {
    this.transientGlowUntil = 0;
    if (this.transientGlowTimer) {
      clearTimeout(this.transientGlowTimer);
      this.transientGlowTimer = null;
    }
  }

  private startTransientGlow(durationMs: number): void {
    const until = Date.now() + Math.max(250, durationMs);
    this.transientGlowUntil = Math.max(this.transientGlowUntil, until);
    if (this.transientGlowTimer) {
      clearTimeout(this.transientGlowTimer);
    }
    this.transientGlowTimer = setTimeout(() => {
      this.transientGlowTimer = null;
      this.syncWindowVisibility();
    }, Math.max(250, this.transientGlowUntil - Date.now() + 60));
  }

  private readonly repositionAnchoredWindows = (): void => {
    this.anchorWindow('notch');
    this.anchorWindow('glow');
    if (!this.userMovedKinds.has('overlay')) {
      this.anchorWindow('overlay');
    }
    if (!this.userMovedKinds.has('sidecar')) {
      this.anchorWindow('sidecar');
    }
    if (this.settingsWindow?.isVisible()) {
      this.positionSettingsWindow();
    }
  };

  private getWindow(kind: WindowKind): BrowserWindowType | null {
    if (kind === 'notch') {
      return this.notchWindow;
    }
    if (kind === 'glow') {
      return this.glowWindow;
    }
    if (kind === 'sidecar') {
      return this.sidecarWindow;
    }
    if (kind === 'settings') {
      return this.settingsWindow;
    }
    return this.overlayWindow;
  }

  private anchorWindow(kind: WindowKind): void {
    const window = this.getWindow(kind);
    if (!window || window.isDestroyed()) {
      return;
    }
    const bounds = window.getBounds();
    const display = screen.getDisplayMatching(bounds);
    const anchorArea = kind === 'glow' || kind === 'notch' ? display.bounds : display.workArea;
    const anchored = getAnchoredWindowBounds(kind, anchorArea, {
      width: bounds.width,
      height: bounds.height
    });
    this.setWindowBounds(kind, window, anchored);
  }

  private updateWindowLayout(senderId: number, payload: WindowLayoutPayload): void {
    const kind = this.getWindowKind(senderId);
    const window = this.getWindow(kind);
    if (!window || window.isDestroyed()) {
      return;
    }

    const display = screen.getDisplayMatching(window.getBounds());
    const anchorArea = kind === 'glow' || kind === 'notch' ? display.bounds : display.workArea;
    const normalized = normalizeWindowSize(kind, anchorArea, {
      width: Number(payload.width) || window.getBounds().width,
      height: Number(payload.height) || window.getBounds().height
    });

    if (kind === 'settings') {
      this.positionSettingsWindow(normalized);
      return;
    }

    if (kind === 'notch' || !this.userMovedKinds.has(kind)) {
      const anchored = getAnchoredWindowBounds(kind, anchorArea, normalized);
      this.setWindowBounds(kind, window, anchored);
      if (kind === 'overlay' && this.settingsWindow?.isVisible()) {
        this.positionSettingsWindow();
      }
      return;
    }

    const current = window.getBounds();
    this.setWindowBounds(kind, window, {
      x: current.x,
      y: current.y,
      width: normalized.width,
      height: normalized.height
    });
    if (kind === 'overlay' && this.settingsWindow?.isVisible()) {
      this.positionSettingsWindow();
    }
  }

  private toggleSettingsWindow(): { visible: boolean } {
    const window = this.settingsWindow;
    if (!window || window.isDestroyed()) {
      return { visible: false };
    }
    if (this.currentSnapshot?.auth.needsAuth) {
      this.hideAllSettingsWindows();
      return { visible: false };
    }
    if (window.isVisible()) {
      window.hide();
      return { visible: false };
    }
    this.positionSettingsWindow();
    window.show();
    window.focus();
    return { visible: true };
  }

  private openSettingsWindow(): { visible: boolean } {
    const window = this.settingsWindow;
    if (!window || window.isDestroyed() || this.currentSnapshot?.auth.needsAuth) {
      return { visible: false };
    }
    this.positionSettingsWindow();
    window.show();
    window.focus();
    return { visible: true };
  }

  private closeSettingsWindow(): { visible: boolean } {
    this.settingsWindow?.hide();
    return { visible: false };
  }

  private applySettingsWindowSizeLimits(window: BrowserWindowType, workArea: { width: number; height: number }): void {
    const maxWidth = Math.max(1, Math.min(980, workArea.width - 24));
    const maxHeight = Math.max(1, Math.min(860, workArea.height - 24));
    window.setMinimumSize(Math.min(560, maxWidth), Math.min(480, maxHeight));
    window.setMaximumSize(maxWidth, maxHeight);
  }

  private positionSettingsWindow(size?: { width: number; height: number }): void {
    const settingsWindow = this.settingsWindow;
    const overlayWindow = this.overlayWindow;
    if (!settingsWindow || settingsWindow.isDestroyed()) {
      return;
    }

    const overlayBounds = overlayWindow && !overlayWindow.isDestroyed()
      ? overlayWindow.getBounds()
      : {
          x: screen.getPrimaryDisplay().workArea.x + Math.round((screen.getPrimaryDisplay().workArea.width - (size?.width ?? settingsWindow.getBounds().width)) / 2),
          y: screen.getPrimaryDisplay().workArea.y + 72,
          width: size?.width ?? settingsWindow.getBounds().width,
          height: size?.height ?? settingsWindow.getBounds().height,
        };
    const display = screen.getDisplayMatching(overlayBounds);
    const normalized = normalizeWindowSize('settings', display.workArea, size ?? settingsWindow.getBounds());
    this.applySettingsWindowSizeLimits(settingsWindow, display.workArea);
    const x = Math.min(
      display.workArea.x + display.workArea.width - normalized.width - 12,
      Math.max(display.workArea.x + 12, overlayBounds.x + overlayBounds.width - normalized.width - 8)
    );
    const y = Math.min(
      display.workArea.y + display.workArea.height - normalized.height - 12,
      Math.max(display.workArea.y + 12, overlayBounds.y + 54)
    );

    this.setWindowBounds('settings', settingsWindow, {
      x,
      y,
      width: normalized.width,
      height: normalized.height
    });
  }

  private hideAllSettingsWindows(): void {
    this.settingsWindow?.hide();
  }

  private async openLogsFolder(): Promise<void> {
    const localAppData = process.env.LOCALAPPDATA;
    const candidates = localAppData
      ? [path.join(localAppData, 'PixelPilot', 'logs'), path.join(process.cwd(), 'logs')]
      : [path.join(process.cwd(), 'logs')];
    const target = candidates.find((candidate) => {
      try {
        return fs.existsSync(candidate);
      } catch {
        return false;
      }
    }) ?? candidates[0];
    await shell.openPath(target);
  }

  private async wrapIpcResult<T>(action: () => Promise<T> | T): Promise<IpcResult<T>> {
    try {
      return successResult(await action());
    } catch (error) {
      return failureResult(error);
    }
  }

  private fireAndForget(task: Promise<unknown>): void {
    void task.catch(() => undefined);
  }

  private setWindowBounds(kind: WindowKind, window: BrowserWindowType, bounds: { x: number; y: number; width: number; height: number }): void {
    this.programmaticMoveKinds.add(kind);
    window.setBounds({
      x: Math.round(bounds.x),
      y: Math.round(bounds.y),
      width: Math.round(bounds.width),
      height: Math.round(bounds.height)
    });
    setTimeout(() => {
      this.programmaticMoveKinds.delete(kind);
    }, 0);
  }
}
