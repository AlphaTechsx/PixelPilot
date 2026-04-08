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
  WindowKind,
  WindowLayoutPayload
} from '../shared/types.js';
import { failureResult, successResult, type IpcResult } from '../shared/ipc-result.js';
import { getAnchoredWindowBounds, normalizeWindowSize } from './window-layout.js';
import { StartupDefaultsStore } from './startup-defaults.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const require = createRequire(import.meta.url);
const electron = require('electron') as typeof import('electron');
const { BrowserWindow, Menu, Tray, app, globalShortcut, ipcMain, nativeImage, screen, shell } = electron;

type InvokeRuntime = (method: string, payload?: Record<string, unknown>) => Promise<Record<string, unknown>>;

export class WindowManager {
  private overlayWindow: BrowserWindowType | null = null;
  private notchWindow: BrowserWindowType | null = null;
  private sidecarWindow: BrowserWindowType | null = null;
  private settingsWindow: BrowserWindowType | null = null;
  private tray: TrayType | null = null;
  private readonly invokeRuntime: InvokeRuntime;
  private readonly startupDefaultsStore: StartupDefaultsStore;
  private readonly kinds = new Map<number, WindowKind>();
  private currentSnapshot: RuntimeSnapshot | null = null;
  private bridgeStatus: BridgeStatus = 'starting';
  private bridgeStatusMessage = 'Starting runtime...';
  private pendingConfirmations = new Map<string, (payload: Record<string, unknown>) => void>();
  private runtimeClickThrough = false;
  private manualClickThroughOverride: boolean | null = null;
  private trayOnly = false;
  private readonly userMovedKinds = new Set<WindowKind>();
  private readonly programmaticMoveKinds = new Set<WindowKind>();

  constructor(invokeRuntime: InvokeRuntime, startupDefaultsStore: StartupDefaultsStore) {
    this.invokeRuntime = invokeRuntime;
    this.startupDefaultsStore = startupDefaultsStore;
    this.registerIpc();
  }

  createWindows(): void {
    this.overlayWindow = this.createWindow('overlay', 920, 180, false);
    this.notchWindow = this.createWindow('notch', 420, 76, true);
    this.sidecarWindow = this.createWindow('sidecar', 390, 420, true);
    this.settingsWindow = this.createWindow('settings', 640, 560, true);
    this.notchWindow.hide();
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
    screen.removeListener('display-metrics-changed', this.repositionAnchoredWindows);
    screen.removeListener('display-added', this.repositionAnchoredWindows);
    screen.removeListener('display-removed', this.repositionAnchoredWindows);
    this.tray?.destroy();
    this.tray = null;
    this.overlayWindow?.destroy();
    this.notchWindow?.destroy();
    this.sidecarWindow?.destroy();
    this.settingsWindow?.destroy();
  }

  getWindowKind(senderId: number): WindowKind {
    return this.kinds.get(senderId) ?? 'overlay';
  }

  applySnapshot(snapshot: RuntimeSnapshot): void {
    this.currentSnapshot = this.decorateSnapshot(snapshot);
    if (!snapshot.backgroundHidden || snapshot.auth.needsAuth) {
      this.trayOnly = false;
    }
    this.runtimeClickThrough = snapshot.auth.needsAuth ? false : snapshot.clickThroughEnabled;
    this.broadcastState(this.currentSnapshot);
    this.syncWindowVisibility();
    this.applyClickThrough();
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
  }

  broadcastEvent(envelope: RuntimeEventEnvelope): void {
    for (const win of [
      this.overlayWindow,
      this.notchWindow,
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
    const window = new BrowserWindow({
      width,
      height,
      icon: this.resolveIconPath(),
      frame: false,
      show: kind === 'overlay',
      transparent: true,
      resizable: false,
      hasShadow: false,
      skipTaskbar,
      alwaysOnTop: true,
      movable:
        kind !== 'notch'
        && kind !== 'settings',
      maximizable: false,
      minimizable: false,
      backgroundColor: '#00000000',
      webPreferences: {
        preload: path.join(__dirname, '../preload/index.js'),
        contextIsolation: true,
        sandbox: false
      }
    });
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
    this.tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: 'Show PixelPilot', click: () => this.fireAndForget(this.setBackgroundHidden(false)) },
        { label: 'Hide to Background', click: () => this.fireAndForget(this.setBackgroundHidden(true)) },
        { type: 'separator' },
        { label: 'Quit', click: () => app.quit() }
      ])
    );
    this.tray.on('click', () => {
      this.fireAndForget(this.setBackgroundHidden(false));
    });
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
          resolver(payload);
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
    const hidden = this.currentSnapshot.backgroundHidden && !this.currentSnapshot.auth.needsAuth;
    const trayOnly = hidden && this.trayOnly;
    if (trayOnly) {
      this.overlayWindow?.hide();
      this.notchWindow?.hide();
      this.settingsWindow?.hide();
    } else if (hidden) {
      this.overlayWindow?.hide();
      this.settingsWindow?.hide();
      this.anchorWindow('notch');
      this.notchWindow?.showInactive();
    } else {
      this.notchWindow?.hide();
      this.overlayWindow?.showInactive();
    }

    if (!hidden && this.currentSnapshot.sidecarVisible && !this.currentSnapshot.auth.needsAuth) {
      this.sidecarWindow?.showInactive();
    } else {
      this.sidecarWindow?.hide();
    }

    if (this.currentSnapshot.auth.needsAuth) {
      this.settingsWindow?.hide();
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
    };
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
    this.overlayWindow.showInactive();
    this.overlayWindow.webContents.send('runtime:confirmation-request', request);
    return new Promise((resolve) => {
      this.pendingConfirmations.set(request.id, resolve);
    });
  }

  private prepareForScreenshot(): Record<string, unknown> {
    const payload = {
      restore_main_window: Boolean(this.currentSnapshot && !this.currentSnapshot.backgroundHidden),
      restore_minimized_notch: Boolean(this.currentSnapshot?.backgroundHidden && !this.trayOnly),
      restore_tray_only: Boolean(this.currentSnapshot?.backgroundHidden && this.trayOnly)
    };
    this.overlayWindow?.hide();
    this.notchWindow?.hide();
    this.sidecarWindow?.hide();
    return payload;
  }

  private restoreAfterScreenshot(payload: Record<string, unknown>): void {
    if (Boolean(payload.restore_tray_only)) {
      this.overlayWindow?.hide();
      this.notchWindow?.hide();
    } else if (Boolean(payload.restore_main_window)) {
      this.overlayWindow?.showInactive();
      this.notchWindow?.hide();
    } else if (Boolean(payload.restore_minimized_notch)) {
      this.notchWindow?.showInactive();
      this.overlayWindow?.hide();
    }
    if (this.currentSnapshot?.sidecarVisible) {
      this.sidecarWindow?.showInactive();
    }
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
    this.trayOnly = false;
    if (this.currentSnapshot) {
      this.currentSnapshot = { ...this.currentSnapshot, backgroundHidden: hidden };
      if (hidden) {
        this.settingsWindow?.hide();
      }
      this.syncWindowVisibility();
      this.broadcastState(this.currentSnapshot);
    }
    try {
      return await this.invokeRuntime('shell.setBackgroundHidden', { hidden });
    } catch (error) {
      this.trayOnly = previousTrayOnly;
      if (previousSnapshot) {
        this.currentSnapshot = previousSnapshot;
        this.syncWindowVisibility();
        this.broadcastState(previousSnapshot);
      }
      throw error;
    }
  }

  private async setTrayOnly(enabled: boolean): Promise<Record<string, unknown>> {
    const previousSnapshot = this.currentSnapshot ? { ...this.currentSnapshot } : null;
    const previousTrayOnly = this.trayOnly;
    const hidden = enabled;

    this.trayOnly = enabled;
    if (this.currentSnapshot) {
      this.currentSnapshot = { ...this.currentSnapshot, backgroundHidden: hidden };
      if (hidden) {
        this.settingsWindow?.hide();
      }
      this.syncWindowVisibility();
      this.broadcastState(this.currentSnapshot);
    }

    try {
      return await this.invokeRuntime('shell.setBackgroundHidden', { hidden });
    } catch (error) {
      this.trayOnly = previousTrayOnly;
      if (previousSnapshot) {
        this.currentSnapshot = previousSnapshot;
        this.syncWindowVisibility();
        this.broadcastState(previousSnapshot);
      }
      throw error;
    }
  }

  private readonly repositionAnchoredWindows = (): void => {
    this.anchorWindow('notch');
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
    const anchored = getAnchoredWindowBounds(kind, display.workArea, {
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
    const normalized = normalizeWindowSize(kind, display.workArea, {
      width: Number(payload.width) || window.getBounds().width,
      height: Number(payload.height) || window.getBounds().height
    });

    if (kind === 'settings') {
      this.positionSettingsWindow(normalized);
      return;
    }

    if (kind === 'notch' || !this.userMovedKinds.has(kind)) {
      const anchored = getAnchoredWindowBounds(kind, display.workArea, normalized);
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
    if (this.currentSnapshot?.backgroundHidden || this.currentSnapshot?.auth.needsAuth) {
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

  private closeSettingsWindow(): { visible: boolean } {
    this.settingsWindow?.hide();
    return { visible: false };
  }

  private positionSettingsWindow(size?: { width: number; height: number }): void {
    const settingsWindow = this.settingsWindow;
    const overlayWindow = this.overlayWindow;
    if (!settingsWindow || settingsWindow.isDestroyed() || !overlayWindow || overlayWindow.isDestroyed()) {
      return;
    }

    const overlayBounds = overlayWindow.getBounds();
    const display = screen.getDisplayMatching(overlayBounds);
    const normalized = normalizeWindowSize('settings', display.workArea, size ?? settingsWindow.getBounds());
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
