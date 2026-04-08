import React, {
  startTransition,
  useDeferredValue,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent
} from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Bot,
  ChevronDown,
  ChevronUp,
  Eye,
  GraduationCap,
  Keyboard,
  LoaderCircle,
  Mic,
  Minus,
  Monitor,
  PanelTop,
  Plus,
  Settings2,
  Shield,
  Waves,
  X
} from 'lucide-react';
import type {
  ActionUpdate,
  AuthState,
  BridgeStatus,
  DoctorReport,
  ExtensionSummary,
  MessageEntry,
  RendererConfirmationRequest,
  RuntimeSnapshot,
  SessionContextSummary,
  SidecarFrame,
  StartupDefaultsSnapshot,
  WindowKind
} from '@shared/types.js';

const shell =
  'glass-panel border border-white/45 bg-white/18 shadow-[0_20px_48px_rgba(15,23,42,0.18)]';
const softShell =
  'soft-panel border border-white/38 bg-white/14 shadow-[0_10px_24px_rgba(15,23,42,0.10)]';
const activeShell =
  'active-panel border border-white/55 bg-white/28 shadow-[0_16px_36px_rgba(15,23,42,0.16)]';

const modes = [
  { id: 'GUIDANCE', label: 'Guidance', hint: 'Read-only tutoring', icon: GraduationCap },
  { id: 'SAFE', label: 'Safe', hint: 'Confirm mutating actions', icon: Shield },
  { id: 'AUTO', label: 'Auto', hint: 'Run actions directly', icon: Bot }
] as const;

const visions = [
  { id: 'ROBO', label: 'Robo' },
  { id: 'OCR', label: 'OCR' }
] as const;

const maxMessages = 40;
const maxActionUpdates = 10;

const emptyAuth: AuthState = {
  signedIn: false,
  directApi: false,
  email: '',
  userId: '',
  backendUrl: '',
  hasApiKey: false,
  needsAuth: true
};

const emptySessionContext: SessionContextSummary = {
  available: false
};

const emptyExtensionSummary: ExtensionSummary = {
  status: 'ready',
  pluginCount: 0,
  mcpServerCount: 0,
  toolCount: 0,
  pluginIds: [],
  mcpServerNames: [],
  toolNames: []
};

const emptyDoctorReport: DoctorReport = {
  status: 'unknown',
  checks: []
};

type WindowLayout = {
  width: number;
  height: number;
};

type SettingsSectionId = 'general' | 'startup' | 'sessions' | 'extensions' | 'diagnostics';

type LiveVisualState =
  | 'disabled'
  | 'off'
  | 'ready'
  | 'connected'
  | 'connecting'
  | 'thinking'
  | 'waiting'
  | 'acting'
  | 'interrupted';

type MicVisualState = 'disabled' | 'idle' | 'listening_user' | 'speaking_assistant';
type CommandBarStatusKind = 'placeholder' | 'reply' | 'status' | 'busy' | 'error';
type CommandBarStatusTone = 'placeholder' | 'status' | 'error';

type CommandBarStatus = {
  kind: CommandBarStatusKind;
  tone: CommandBarStatusTone;
  text: string;
};

function clamp(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(1, value));
}

function trimMessages(entries: MessageEntry[]): MessageEntry[] {
  return entries.slice(-maxMessages);
}

function trimActionUpdates(updates: ActionUpdate[]): ActionUpdate[] {
  return updates.slice(-maxActionUpdates);
}

function useWindowLayout(layout: WindowLayout | null): void {
  useEffect(() => {
    if (!layout) {
      return;
    }
    void window.pixelPilot.updateWindowLayout({
      width: Math.max(1, Math.round(layout.width)),
      height: Math.max(1, Math.round(layout.height))
    });
  }, [layout?.width, layout?.height]);
}

function useMeasuredWindowLayout<T extends HTMLElement>(
  ref: React.RefObject<T | null>,
  fallbackLayout: WindowLayout,
  deps: React.DependencyList = []
): void {
  const [layout, setLayout] = useState<WindowLayout>(fallbackLayout);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) {
      setLayout(fallbackLayout);
      return;
    }

    const measure = (): void => {
      const rect = element.getBoundingClientRect();
      const measuredWidth = Math.max(rect.width || 0, element.scrollWidth || 0, fallbackLayout.width);
      const measuredHeight = Math.max(rect.height || 0, element.scrollHeight || 0, fallbackLayout.height);
      const width = Math.ceil(measuredWidth);
      const height = Math.ceil(measuredHeight);
      setLayout((current) => {
        if (current.width === width && current.height === height) {
          return current;
        }
        return { width, height };
      });
    };

    measure();

    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(() => {
        measure();
      });
      observer.observe(element);
      return () => {
        observer.disconnect();
      };
    }

    return undefined;
  }, [ref, fallbackLayout.width, fallbackLayout.height, ...deps]);

  useWindowLayout(layout);
}

function normalizeEntry(entry: MessageEntry): MessageEntry {
  return {
    id: String(entry.id || crypto.randomUUID()),
    kind: String(entry.kind || 'system'),
    text: String(entry.text || ''),
    speaker: String(entry.speaker || entry.kind || 'system'),
    final: Boolean(entry.final)
  };
}

function mergeTranscript(entries: MessageEntry[], payload: Record<string, unknown>): MessageEntry[] {
  const text = String(payload.text || '').trim();
  if (!text) {
    return entries;
  }
  const speaker = String(payload.speaker || 'assistant').trim().toLowerCase() || 'assistant';
  const final = Boolean(payload.final);
  const kind = speaker === 'user' ? 'user' : 'assistant';
  const next = [...entries];
  const activeIndex = [...next]
    .map((entry, index) => ({ entry, index }))
    .reverse()
    .find(({ entry }) => entry.speaker === speaker && !entry.final)?.index;

  if (activeIndex === undefined) {
    next.push({
      id: crypto.randomUUID(),
      kind,
      text,
      speaker,
      final
    });
    return trimMessages(next);
  }

  next[activeIndex] = {
    ...next[activeIndex],
    kind,
    speaker,
    text,
    final
  };
  return trimMessages(next);
}

function mergeActionUpdate(updates: ActionUpdate[], payload: Record<string, unknown>): ActionUpdate[] {
  const update: ActionUpdate = {
    action_id: typeof payload.action_id === 'string' ? payload.action_id : undefined,
    name: typeof payload.name === 'string' ? payload.name : undefined,
    status: typeof payload.status === 'string' ? payload.status : undefined,
    message: typeof payload.message === 'string' ? payload.message : undefined,
    error: typeof payload.error === 'string' ? payload.error : undefined,
    done: typeof payload.done === 'boolean' ? payload.done : undefined
  };
  const key = update.action_id || update.name || crypto.randomUUID();
  const next = [...updates];
  const existingIndex = next.findIndex((item) => (item.action_id || item.name) === key);
  if (existingIndex >= 0) {
    next[existingIndex] = {
      ...next[existingIndex],
      ...update
    };
    return trimActionUpdates(next);
  }
  next.push(update);
  return trimActionUpdates(next);
}

function patchSnapshot(
  snapshot: RuntimeSnapshot | null,
  patch: Partial<RuntimeSnapshot>
): RuntimeSnapshot | null {
  if (!snapshot) {
    return snapshot;
  }
  return {
    ...snapshot,
    ...patch
  };
}

function parseSessionContext(payload: unknown): SessionContextSummary {
  if (!payload || typeof payload !== 'object') {
    return emptySessionContext;
  }
  const record = payload as Record<string, unknown>;
  return {
    available: Boolean(record.available),
    workspaceFingerprint: typeof record.workspaceFingerprint === 'string' ? record.workspaceFingerprint : undefined,
    sessionId: typeof record.sessionId === 'string' ? record.sessionId : undefined,
    lastActivityAt: typeof record.lastActivityAt === 'string' ? record.lastActivityAt : undefined,
    summaryText: typeof record.summaryText === 'string' ? record.summaryText : undefined,
    resumePayload:
      record.resumePayload && typeof record.resumePayload === 'object'
        ? (record.resumePayload as Record<string, unknown>)
        : undefined,
    tail: Array.isArray(record.tail) ? (record.tail as Record<string, unknown>[]) : undefined,
    logPath: typeof record.logPath === 'string' ? record.logPath : undefined
  };
}

function parseExtensionSummary(payload: unknown): ExtensionSummary {
  if (!payload || typeof payload !== 'object') {
    return emptyExtensionSummary;
  }
  const record = payload as Record<string, unknown>;
  return {
    status: typeof record.status === 'string' ? record.status : 'ready',
    pluginCount: Number(record.pluginCount || 0),
    mcpServerCount: Number(record.mcpServerCount || 0),
    toolCount: Number(record.toolCount || 0),
    pluginIds: Array.isArray(record.pluginIds) ? record.pluginIds.map((value) => String(value)) : [],
    mcpServerNames: Array.isArray(record.mcpServerNames) ? record.mcpServerNames.map((value) => String(value)) : [],
    toolNames: Array.isArray(record.toolNames) ? record.toolNames.map((value) => String(value)) : []
  };
}

function parseDoctorReport(payload: unknown): DoctorReport {
  if (!payload || typeof payload !== 'object') {
    return emptyDoctorReport;
  }
  const record = payload as Record<string, unknown>;
  return {
    status: typeof record.status === 'string' ? record.status : 'unknown',
    checks: Array.isArray(record.checks)
      ? record.checks
          .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
          .map((item) => ({
            name: typeof item.name === 'string' ? item.name : 'Unknown check',
            status: typeof item.status === 'string' ? item.status : 'unknown',
            summary: typeof item.summary === 'string' ? item.summary : '',
            details: item.details && typeof item.details === 'object'
              ? (item.details as Record<string, unknown>)
              : {}
          }))
      : []
  };
}

function renderDoctorReportText(report: DoctorReport): string {
  const lines = [`PixelPilot doctor: ${String(report.status || 'unknown').toUpperCase()}`];
  for (const check of report.checks) {
    lines.push(`- ${check.name}: ${check.status}${check.summary ? ` - ${check.summary}` : ''}`);
  }
  return lines.join('\n');
}

async function copyTextToClipboard(text: string): Promise<void> {
  const value = String(text || '');
  if (navigator?.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const copied = typeof document.execCommand === 'function' && document.execCommand('copy');
  document.body.removeChild(textarea);
  if (!copied) {
    throw new Error('Clipboard access is unavailable right now.');
  }
}

function isBridgeBusyStatus(status: BridgeStatus): boolean {
  return status === 'starting' || status === 'recovering';
}

function bridgeStatusText(snapshot: RuntimeSnapshot): string {
  const status = snapshot.bridgeStatus;
  const message = String(snapshot.bridgeStatusMessage || '').trim();
  if (status === 'starting') {
    return message || 'Starting runtime...';
  }
  if (status === 'recovering') {
    return message || 'Reconnecting runtime...';
  }
  if (status === 'failed') {
    return message || 'PixelPilot lost the runtime connection.';
  }
  return '';
}

function speakerLabel(entry: MessageEntry): string {
  if (entry.speaker === 'user' || entry.kind === 'user') {
    return 'You';
  }
  if (entry.speaker === 'assistant' || entry.kind === 'assistant') {
    return 'PixelPilot';
  }
  if (entry.kind === 'activity') {
    return 'Activity';
  }
  if (entry.kind === 'error') {
    return 'System Alert';
  }
  return 'System';
}

function actionTone(status: string | undefined): string {
  const normalized = String(status || '').toLowerCase();
  if (normalized.includes('fail') || normalized.includes('error') || normalized.includes('cancel')) {
    return 'bg-rose-400';
  }
  if (normalized.includes('done') || normalized.includes('complete') || normalized.includes('success')) {
    return 'bg-emerald-500';
  }
  if (normalized.includes('run') || normalized.includes('progress') || normalized.includes('start')) {
    return 'bg-sky-500';
  }
  return 'bg-amber-400';
}

function messageBubbleClass(entry: MessageEntry): string {
  if (entry.kind === 'user') {
    return 'ml-auto max-w-[76%] px-1 py-0.5 text-right text-[14px] leading-6 text-sky-700';
  }
  if (entry.kind === 'assistant') {
    return 'max-w-[76%] px-1 py-0.5 text-[14px] leading-6 text-sky-600';
  }
  if (entry.kind === 'error') {
    return 'max-w-full px-1 py-0.5 text-[14px] font-semibold leading-6 text-rose-700';
  }
  if (entry.kind === 'activity') {
    return 'max-w-full px-1 py-0.5 text-[13px] italic leading-6 text-slate-500';
  }
  return 'max-w-full px-1 py-0.5 text-[13px] italic leading-6 text-slate-600';
}

function actionUpdateSummary(update: ActionUpdate): string {
  if (update.error) {
    return String(update.error).trim();
  }
  const message = String(update.message || '').trim();
  const name = String(update.name || '').trim();
  const status = String(update.status || '').trim();
  if (message) {
    return message;
  }
  if (name && status) {
    return `${name} · ${humanizeState(status)}`;
  }
  if (name) {
    return name;
  }
  if (status) {
    return humanizeState(status);
  }
  return 'Working on the current task...';
}

function buildThinkingState(
  snapshot: RuntimeSnapshot,
  updates: ActionUpdate[]
): { summary: string; lines: string[] } {
  const lines = updates
    .filter((update) => Boolean(update.error || update.message || update.name || update.status))
    .slice(-8)
    .map(actionUpdateSummary)
    .filter((line, index, source) => Boolean(line) && (index === 0 || line !== source[index - 1]));

  if (lines.length > 0) {
    return {
      summary: lines[lines.length - 1],
      lines
    };
  }

  const state = String(snapshot.liveSessionState || '').trim().toLowerCase();
  if (state === 'acting') {
    return { summary: 'Working on the current task...', lines: ['Working on the current task...'] };
  }
  if (state === 'waiting') {
    return { summary: 'Waiting for the current task to finish...', lines: ['Waiting for the current task to finish...'] };
  }
  if (state === 'interrupted') {
    return { summary: 'Interrupted. Waiting for your next instruction...', lines: ['Interrupted. Waiting for your next instruction...'] };
  }
  return { summary: '', lines: [] };
}

function workspaceIcon(workspace: RuntimeSnapshot['workspace']): React.JSX.Element {
  return workspace === 'agent' ? <Monitor className="h-4 w-4" /> : <Keyboard className="h-4 w-4" />;
}

function workspaceBadgeLabel(snapshot: RuntimeSnapshot): string {
  if (snapshot.workspace === 'agent') {
    return snapshot.agentViewRequested
      ? 'Current workspace: Agent Desktop. Click to hide the agent view'
      : 'Current workspace: Agent Desktop. Click to show the agent view';
  }
  return 'Current workspace: User Desktop';
}

function isBusyLiveState(state: string): boolean {
  return ['connecting', 'thinking', 'waiting', 'acting'].includes(String(state || '').trim().toLowerCase());
}

function isDoneStatus(status: string | undefined): boolean {
  const normalized = String(status || '').trim().toLowerCase();
  return (
    normalized.includes('done') ||
    normalized.includes('complete') ||
    normalized.includes('success') ||
    normalized.includes('finished')
  );
}

function isErrorStatus(status: string | undefined): boolean {
  const normalized = String(status || '').trim().toLowerCase();
  return normalized.includes('error') || normalized.includes('fail') || normalized.includes('cancel');
}

function humanizeState(state: string): string {
  const normalized = String(state || '').trim().toLowerCase();
  if (!normalized) {
    return 'Idle';
  }
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function wakeWordStatusDescription(snapshot: RuntimeSnapshot): string {
  const phrase = String(snapshot.wakeWordPhrase || '').trim() || 'Hey Pixie';
  const state = String(snapshot.wakeWordState || '').trim().toLowerCase();
  const liveState = String(snapshot.liveSessionState || '').trim().toLowerCase();
  if (!snapshot.wakeWordEnabled || state === 'disabled') {
    return 'Wake word is off';
  }
  if (state === 'unavailable') {
    return snapshot.wakeWordUnavailableReason || 'Wake word unavailable';
  }
  if (state === 'starting') {
    return `Arming "${phrase}"...`;
  }
  if (state === 'armed') {
    if (liveState === 'disconnected') {
      return `Wake word is listening. Say "${phrase}" to reconnect AI`;
    }
    return `Wake word is listening for "${phrase}"`;
  }
  if (snapshot.liveVoiceActive) {
    return 'Wake word paused while voice is active';
  }
  return 'Wake word paused';
}

function inputPlaceholder(snapshot: RuntimeSnapshot): string {
  if (!snapshot.liveAvailable) {
    return snapshot.liveUnavailableReason || 'PixelPilot Live unavailable';
  }
  if (snapshot.liveVoiceActive) {
    return 'Type or speak while the mic is active...';
  }
  if (snapshot.wakeWordEnabled && String(snapshot.wakeWordState || '').trim().toLowerCase() === 'armed') {
    if (String(snapshot.liveSessionState || '').trim().toLowerCase() === 'disconnected') {
      return `Type a command or say "${snapshot.wakeWordPhrase}" to reconnect PixelPilot...`;
    }
    return `Type a command or say "${snapshot.wakeWordPhrase}"...`;
  }
  if (String(snapshot.liveSessionState || '').trim().toLowerCase() === 'disconnected') {
    return 'Type a command to reconnect PixelPilot...';
  }
  return 'Type a command...';
}

function latestAssistantEntry(entries: MessageEntry[], finalOnly = true): MessageEntry | null {
  const match = [...entries].reverse().find((entry) => {
    const kind = String(entry.kind || '').trim().toLowerCase();
    const speaker = String(entry.speaker || '').trim().toLowerCase();
    if (!(kind === 'assistant' || speaker === 'assistant')) {
      return false;
    }
    if (finalOnly && !entry.final) {
      return false;
    }
    return Boolean(String(entry.text || '').trim());
  });
  return match ?? null;
}

function latestRelevantActionUpdate(updates: ActionUpdate[]): ActionUpdate | null {
  const match = [...updates].reverse().find((update) => {
    return Boolean(update.error || update.message || update.name || update.status);
  });
  return match ?? null;
}

function buildCommandBarStatus(
  snapshot: RuntimeSnapshot,
  messages: MessageEntry[],
  actionUpdates: ActionUpdate[],
  busyText: string,
  localError: string,
  runtimeError: string
): CommandBarStatus {
  const bridgeText = bridgeStatusText(snapshot);
  if (isBridgeBusyStatus(snapshot.bridgeStatus) && bridgeText) {
    return { kind: 'busy', tone: 'status', text: bridgeText };
  }

  const errorText = String(localError || runtimeError || '').trim();
  if (errorText) {
    return { kind: 'error', tone: 'error', text: errorText };
  }

  if (snapshot.bridgeStatus === 'failed' && bridgeText) {
    return { kind: 'error', tone: 'error', text: bridgeText };
  }

  const pendingText = String(busyText || '').trim();
  if (pendingText) {
    return { kind: 'busy', tone: 'status', text: pendingText };
  }

  const liveState = String(snapshot.liveSessionState || '').trim().toLowerCase();
  const latestAction = latestRelevantActionUpdate(actionUpdates);
  if (latestAction) {
    const summary = actionUpdateSummary(latestAction);
    if (summary) {
      if (latestAction.error || isErrorStatus(latestAction.status)) {
        return { kind: 'error', tone: 'error', text: summary };
      }
      const actionInProgress =
        latestAction.done !== true &&
        !isDoneStatus(latestAction.status) &&
        !isErrorStatus(latestAction.status);
      if (actionInProgress || isBusyLiveState(liveState)) {
        return { kind: 'status', tone: 'status', text: summary };
      }
    }
  }

  const thinkingState = buildThinkingState(snapshot, actionUpdates);
  if (thinkingState.summary && isBusyLiveState(liveState)) {
    return { kind: 'status', tone: 'status', text: thinkingState.summary };
  }

  const assistantReply = latestAssistantEntry(messages, true) || latestAssistantEntry(messages, false);
  if (assistantReply) {
    return { kind: 'reply', tone: 'status', text: assistantReply.text.trim() };
  }

  return { kind: 'placeholder', tone: 'placeholder', text: inputPlaceholder(snapshot) };
}

function hasOngoingLiveTurn(
  snapshot: RuntimeSnapshot,
  actionUpdates: ActionUpdate[],
  busyText: string
): boolean {
  if (String(busyText || '').trim().toLowerCase() === 'submitting...') {
    return true;
  }

  const liveState = String(snapshot.liveSessionState || '').trim().toLowerCase();
  if (['thinking', 'waiting', 'acting'].includes(liveState)) {
    return true;
  }

  const latestAction = latestRelevantActionUpdate(actionUpdates);
  if (!latestAction) {
    return false;
  }

  return (
    latestAction.done !== true &&
    !isDoneStatus(latestAction.status) &&
    !isErrorStatus(latestAction.status)
  );
}

function liveButtonPresentation(
  snapshot: RuntimeSnapshot,
  pending = false
): { state: LiveVisualState; label: string; disabled: boolean; description: string } {
  if (!snapshot.liveAvailable) {
    return {
      state: 'disabled',
      label: 'Reconnect live session',
      disabled: true,
      description: snapshot.liveUnavailableReason || 'PixelPilot Live unavailable'
    };
  }

  const state = String(snapshot.liveSessionState || '').trim().toLowerCase();
  if (state === 'disconnected') {
    const wakeWordState = String(snapshot.wakeWordState || '').trim().toLowerCase();
    let description = 'AI is disconnected. Type or start voice to reconnect.';
    if (snapshot.wakeWordEnabled && wakeWordState === 'armed') {
      description = `AI is disconnected. Say "${snapshot.wakeWordPhrase}" to reconnect.`;
    } else if (snapshot.wakeWordEnabled && wakeWordState === 'starting') {
      description = `AI is disconnected. Arming "${snapshot.wakeWordPhrase}"...`;
    } else if (snapshot.wakeWordEnabled && wakeWordState === 'unavailable') {
      description = snapshot.wakeWordUnavailableReason || 'AI is disconnected. Wake word unavailable.';
    }
    return {
      state: pending ? 'connecting' : 'ready',
      label: 'Reconnect live session',
      disabled: false,
      description: pending ? 'Reconnecting Gemini Live...' : description
    };
  }

  if (pending || state === 'connecting') {
    return { state: 'connecting', label: 'Disconnect live session', disabled: false, description: 'Connecting to AI...' };
  }
  if (state === 'thinking') {
    return { state: 'thinking', label: 'Disconnect live session', disabled: false, description: 'AI is thinking' };
  }
  if (state === 'waiting') {
    return { state: 'waiting', label: 'Disconnect live session', disabled: false, description: 'Waiting for the current action' };
  }
  if (state === 'acting') {
    return { state: 'acting', label: 'Disconnect live session', disabled: false, description: 'AI is working on the task' };
  }
  if (state === 'interrupted') {
    return { state: 'interrupted', label: 'Disconnect live session', disabled: false, description: 'AI was interrupted' };
  }
  return { state: 'connected', label: 'Disconnect live session', disabled: false, description: 'AI is connected' };
}

function micButtonPresentation(
  snapshot: RuntimeSnapshot,
  pending = false
): { state: MicVisualState; label: string; disabled: boolean; description: string } {
  if (!snapshot.liveAvailable) {
    return {
      state: 'disabled',
      label: 'Enable voice input',
      disabled: true,
      description: snapshot.liveUnavailableReason || 'PixelPilot Live unavailable'
    };
  }

  const disconnected = String(snapshot.liveSessionState || '').trim().toLowerCase() === 'disconnected';
  if (disconnected && !snapshot.liveVoiceActive) {
    return {
      state: pending ? 'listening_user' : 'idle',
      label: 'Reconnect and start voice',
      disabled: false,
      description: pending ? 'Reconnecting and starting voice...' : 'Reconnect and start voice'
    };
  }

  const voiceArmed = snapshot.liveVoiceActive || pending;
  if (snapshot.assistantAudioLevel > 0.02) {
    return {
      state: 'speaking_assistant',
      label: voiceArmed ? 'Mute voice input' : 'Enable voice input',
      disabled: false,
      description: voiceArmed ? 'Assistant is speaking while voice input is active' : 'Assistant is speaking'
    };
  }

  if (voiceArmed) {
    return {
      state: 'listening_user',
      label: 'Mute voice input',
      disabled: false,
      description: pending ? 'Starting voice...' : 'Voice input is active'
    };
  }

  return {
    state: 'idle',
    label: 'Enable voice input',
    disabled: false,
    description: 'Start live voice input'
  };
}

function buildNotchProgress(
  snapshot: RuntimeSnapshot,
  actionUpdates: ActionUpdate[],
  runtimeError: string
): { text: string; busy: boolean; tone: 'idle' | 'active' | 'error' } {
  const errorText = runtimeError.trim();
  if (errorText) {
    return { text: errorText, busy: false, tone: 'error' };
  }

  const latestAction = [...actionUpdates].reverse().find((update) => {
    return Boolean(update.error || update.message || update.name || update.status);
  });

  if (latestAction) {
    if (latestAction.error) {
      return { text: latestAction.error, busy: false, tone: 'error' };
    }

    const text =
      latestAction.message ||
      (latestAction.name
        ? latestAction.status
          ? `${latestAction.name} · ${humanizeState(latestAction.status)}`
          : latestAction.name
        : latestAction.status
          ? humanizeState(latestAction.status)
          : '');

    if (text) {
      const busy = latestAction.done !== true && !isDoneStatus(latestAction.status) && !isErrorStatus(latestAction.status);
      if (isErrorStatus(latestAction.status)) {
        return { text, busy: false, tone: 'error' };
      }
      if (busy) {
        return { text, busy: true, tone: 'active' };
      }
    }
  }

  if (!snapshot.liveAvailable) {
    return {
      text: snapshot.liveUnavailableReason || 'Live is unavailable right now.',
      busy: false,
      tone: 'error'
    };
  }

  if (snapshot.liveVoiceActive) {
    return { text: 'Listening for your next instruction...', busy: false, tone: 'active' };
  }

  if (snapshot.wakeWordEnabled) {
    const wakeWordState = String(snapshot.wakeWordState || '').trim().toLowerCase();
    if (wakeWordState === 'armed') {
      if (String(snapshot.liveSessionState || '').trim().toLowerCase() === 'disconnected') {
        return {
          text: `Gemini Live is disconnected. Wake word is listening for "${snapshot.wakeWordPhrase}".`,
          busy: false,
          tone: 'active'
        };
      }
      return { text: `Wake word is listening for "${snapshot.wakeWordPhrase}".`, busy: false, tone: 'active' };
    }
    if (wakeWordState === 'starting') {
      return { text: `Arming "${snapshot.wakeWordPhrase}"...`, busy: true, tone: 'active' };
    }
    if (wakeWordState === 'paused') {
      return { text: wakeWordStatusDescription(snapshot), busy: false, tone: 'idle' };
    }
    if (wakeWordState === 'unavailable' && snapshot.wakeWordUnavailableReason) {
      return { text: snapshot.wakeWordUnavailableReason, busy: false, tone: 'error' };
    }
  }

  const stateText: Record<string, string> = {
    connecting: 'Connecting to PixelPilot Live...',
    thinking: 'Thinking about the current task...',
    waiting: 'Waiting for the current action...',
    acting: 'Working on the current task...',
    interrupted: 'Interrupted. Waiting for your next instruction...',
    listening: 'Ready for your next instruction.'
  };

  const liveState = String(snapshot.liveSessionState || '').trim().toLowerCase();
  if (stateText[liveState]) {
    return {
      text: stateText[liveState],
      busy: isBusyLiveState(liveState),
      tone: isBusyLiveState(liveState) ? 'active' : 'idle'
    };
  }

  if (liveState === 'disconnected') {
    return { text: 'Gemini Live is disconnected. Tap to reconnect.', busy: false, tone: 'idle' };
  }

  return { text: 'PixelPilot is running in the background.', busy: false, tone: 'idle' };
}

function PixelPilotLogo({ className = '' }: { className?: string }): React.JSX.Element {
  const gradientId = useId().replace(/:/g, '');

  return (
    <svg
      viewBox="0 -20 250 270"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <defs>
        <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#007acc" />
          <stop offset="100%" stopColor="#4ec9b0" />
        </linearGradient>
      </defs>
      <path
        d="M0 0h160c40 0 72 32 72 72v28c0 40-32 72-72 72H60v78H0V0Z"
        fill={`url(#${gradientId})`}
      />
      <rect x="60" y="60" width="100" height="52" rx="4" fill="#0f172a" />
      <path d="M180 -20h70V50Z" fill="#fff" />
    </svg>
  );
}

function GlassPanel({
  className = '',
  children
}: {
  className?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return <div className={`${shell} rounded-[28px] ${className}`}>{children}</div>;
}

function SoftPanel({
  className = '',
  children
}: {
  className?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return <div className={`${softShell} rounded-[24px] ${className}`}>{children}</div>;
}

function IconButton({
  label,
  active = false,
  disabled = false,
  onClick,
  compact = false,
  children
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  compact?: boolean;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={[
        'no-drag flex items-center justify-center text-slate-800 transition-all',
        compact ? 'h-8 w-10 rounded-xl' : 'h-10 w-10 rounded-2xl',
        active ? activeShell : softShell,
        disabled ? 'cursor-not-allowed opacity-45' : 'hover:bg-white/24'
      ].join(' ')}
    >
      {children}
    </button>
  );
}

function LiveModeButton({
  snapshot,
  pending = false,
  compact = false,
  disabled = false,
  onClick
}: {
  snapshot: RuntimeSnapshot;
  pending?: boolean;
  compact?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}): React.JSX.Element {
  const presentation = liveButtonPresentation(snapshot, pending);
  const isDisabled = presentation.disabled || disabled;
  const toneClasses: Record<LiveVisualState, string> = {
    disabled: 'text-slate-400',
    off: 'text-slate-600',
    ready: 'text-cyan-700',
    connected: 'text-emerald-700',
    connecting: 'text-sky-700',
    thinking: 'text-sky-700',
    waiting: 'text-slate-600',
    acting: 'text-amber-700',
    interrupted: 'text-amber-700'
  };
  const dotClasses: Record<LiveVisualState, string> = {
    disabled: 'bg-slate-300',
    off: 'bg-slate-400',
    ready: 'bg-cyan-400',
    connected: 'bg-emerald-400',
    connecting: 'bg-sky-400',
    thinking: 'bg-sky-400',
    waiting: 'bg-slate-400',
    acting: 'bg-amber-400',
    interrupted: 'bg-amber-500'
  };
  const animated = ['connecting', 'thinking', 'waiting', 'acting'].includes(presentation.state);

  return (
    <button
      type="button"
      aria-label={presentation.label}
      title={presentation.description}
      disabled={isDisabled}
      onClick={onClick}
      className={[
        'no-drag relative flex items-center justify-center text-slate-800 transition-all',
        compact ? 'h-8 w-10 rounded-xl' : 'h-10 w-10 rounded-2xl',
        presentation.state === 'off' || presentation.state === 'disabled' ? softShell : activeShell,
        isDisabled ? 'cursor-not-allowed opacity-45' : 'hover:bg-white/24'
      ].join(' ')}
    >
      {animated && (
        <motion.span
          aria-hidden="true"
          className="pointer-events-none absolute inset-[7px] rounded-full border border-current/25"
          animate={{ scale: [0.92, 1.08, 0.92], opacity: [0.25, 0.65, 0.25] }}
          transition={{ duration: presentation.state === 'waiting' ? 1.8 : 1.2, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
      <span className={`relative ${toneClasses[presentation.state]}`}>
        <Waves className="h-4 w-4" />
        <span className={`absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full ${dotClasses[presentation.state]}`} />
      </span>
    </button>
  );
}

function MicControlButton({
  snapshot,
  pending = false,
  compact = false,
  disabled = false,
  onClick
}: {
  snapshot: RuntimeSnapshot;
  pending?: boolean;
  compact?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}): React.JSX.Element {
  const presentation = micButtonPresentation(snapshot, pending);
  const isDisabled = presentation.disabled || disabled;
  const effectiveLevel =
    presentation.state === 'speaking_assistant'
      ? Math.max(0.2, clamp(snapshot.assistantAudioLevel))
      : presentation.state === 'listening_user'
        ? Math.max(0.1, clamp(snapshot.userAudioLevel))
        : 0;
  const animated = presentation.state === 'listening_user' || presentation.state === 'speaking_assistant';
  const toneClass =
    presentation.state === 'disabled'
      ? 'text-slate-400'
      : presentation.state === 'speaking_assistant'
        ? 'text-sky-700'
        : presentation.state === 'listening_user'
          ? 'text-emerald-700'
          : 'text-slate-600';
  const ringClass =
    presentation.state === 'speaking_assistant'
      ? 'border-sky-400/45'
      : presentation.state === 'listening_user'
        ? 'border-emerald-400/45'
        : 'border-white/0';
  const glowClass =
    presentation.state === 'speaking_assistant'
      ? 'bg-sky-400/18'
      : presentation.state === 'listening_user'
        ? 'bg-emerald-400/18'
        : 'bg-transparent';
  const coreClass =
    presentation.state === 'speaking_assistant'
      ? 'bg-sky-50/85'
      : presentation.state === 'listening_user'
        ? 'bg-emerald-50/85'
        : 'bg-transparent';

  return (
    <button
      type="button"
      aria-label={presentation.label}
      title={presentation.description}
      aria-pressed={presentation.state === 'listening_user'}
      disabled={isDisabled}
      onClick={onClick}
      className={[
        'no-drag relative flex items-center justify-center text-slate-800 transition-all',
        compact ? 'h-8 w-10 rounded-xl' : 'h-10 w-10 rounded-2xl',
        presentation.state === 'idle' || presentation.state === 'disabled' ? softShell : activeShell,
        isDisabled ? 'cursor-not-allowed opacity-45' : 'hover:bg-white/24'
      ].join(' ')}
    >
      <span
        aria-hidden="true"
        className={`pointer-events-none absolute inset-[6px] rounded-[inherit] transition-all ${glowClass}`}
      />
      {animated && (
        <motion.span
          aria-hidden="true"
          className={`pointer-events-none absolute inset-[7px] rounded-full border ${ringClass}`}
          animate={{
            scale: presentation.state === 'speaking_assistant' ? [0.92, 1.2 + effectiveLevel * 0.12, 0.92] : [0.94, 1.08 + effectiveLevel * 0.08, 0.94],
            opacity: [0.25, 0.75, 0.25]
          }}
          transition={{ duration: presentation.state === 'speaking_assistant' ? 0.95 : 1.25, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
      {animated && (
        <motion.span
          aria-hidden="true"
          className={`pointer-events-none absolute inset-[4px] rounded-full border ${ringClass}`}
          animate={{
            scale: presentation.state === 'speaking_assistant' ? [0.88, 1.34 + effectiveLevel * 0.18, 0.88] : [0.9, 1.18 + effectiveLevel * 0.14, 0.9],
            opacity: [0.18, 0.45, 0.18]
          }}
          transition={{
            duration: presentation.state === 'speaking_assistant' ? 1.1 : 1.45,
            repeat: Infinity,
            ease: 'easeInOut',
            delay: 0.18
          }}
        />
      )}
      <span className={`relative flex h-[70%] w-[70%] items-center justify-center rounded-[inherit] transition-all ${toneClass} ${coreClass}`}>
        <Mic className="h-4 w-4" />
      </span>
      {(presentation.state === 'listening_user' || presentation.state === 'speaking_assistant') && (
        <motion.span
          aria-hidden="true"
          className={[
            'pointer-events-none absolute bottom-1.5 right-1.5 h-1.5 w-1.5 rounded-full',
            presentation.state === 'speaking_assistant' ? 'bg-sky-500' : 'bg-emerald-500'
          ].join(' ')}
          animate={{ scale: [0.9, 1.45, 0.9], opacity: [0.5, 1, 0.5] }}
          transition={{ duration: 0.8, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
    </button>
  );
}

function SegmentedButton({
  label,
  active = false,
  onClick,
  disabled = false,
  icon
}: {
  label: string;
  active?: boolean;
  onClick?: () => void;
  disabled?: boolean;
  icon?: React.ReactNode;
}): React.JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={[
        'no-drag inline-flex h-10 items-center gap-2 rounded-2xl px-3 text-sm font-medium text-slate-800 transition-all',
        active ? activeShell : softShell,
        disabled ? 'cursor-not-allowed opacity-45' : 'hover:bg-white/24'
      ].join(' ')}
    >
      {icon}
      {label}
    </button>
  );
}

function MenuItemButton({
  label,
  active = false,
  disabled = false,
  onClick
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}): React.JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={[
        'no-drag flex w-full items-center justify-between rounded-lg px-3.5 py-2 text-left text-[13px] transition-all',
        active ? 'bg-[#e2e8f0] text-slate-900' : 'bg-transparent text-slate-800',
        disabled ? 'cursor-not-allowed opacity-45' : 'hover:bg-[#e2e8f0]'
      ].join(' ')}
    >
      <span>{label}</span>
      <span className={active ? 'text-slate-700' : 'text-transparent'}>{active ? '\u2713' : ''}</span>
    </button>
  );
}

function StatusPill({
  label,
  active = false,
  icon
}: {
  label: string;
  active?: boolean;
  icon?: React.ReactNode;
}): React.JSX.Element {
  return (
    <div
      className={[
        'inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.16em]',
        active
          ? 'border-white/50 bg-white/46 text-slate-800'
          : 'border-white/34 bg-white/24 text-slate-600',
        'backdrop-blur-xl'
      ].join(' ')}
    >
      {icon}
      <span>{label}</span>
    </div>
  );
}

function isSettingsWindowKind(kind: WindowKind | null): boolean {
  return kind === 'settings';
}

function LoadingShell({
  windowKind,
  statusText,
}: {
  windowKind: WindowKind | null;
  statusText?: string;
}): React.JSX.Element {
  const widthClass =
    windowKind === 'notch'
      ? 'w-[420px]'
      : windowKind === 'sidecar'
        ? 'w-[380px]'
        : windowKind === 'settings'
          ? 'w-[640px]'
          : 'w-[560px]';
  useWindowLayout(
    windowKind === 'notch'
      ? { width: 420, height: 88 }
      : windowKind === 'sidecar'
        ? { width: 380, height: 320 }
        : windowKind === 'settings'
          ? { width: 640, height: 560 }
          : { width: 560, height: 128 }
  );
  return (
    <div className="relative flex w-full items-start justify-center p-3 text-slate-900">
      <motion.div
        className={`relative ${widthClass}`}
        initial={{ opacity: 0, y: -12 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <GlassPanel className="drag-region p-4">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-[20px] border border-white/40 bg-white/26">
              <PixelPilotLogo className="h-6 w-6" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-slate-900">Starting PixelPilot</div>
              <div className="text-sm text-slate-600">{statusText || 'Preparing your secure workspace.'}</div>
            </div>
            <LoaderCircle className="h-5 w-5 animate-spin text-slate-700" />
          </div>
        </GlassPanel>
      </motion.div>
    </div>
  );
}

function startupDefaultsSourceLabel(source: StartupDefaultsSnapshot['source']): string {
  if (source === 'persisted') {
    return 'Using saved startup defaults';
  }
  if (source === 'runtime') {
    return 'Using current app values';
  }
  return 'Using fallback defaults';
}

function StartupDefaultsSection({
  snapshot,
  disabled = false
}: {
  snapshot: RuntimeSnapshot;
  disabled?: boolean;
}): React.JSX.Element {
  const [localError, setLocalError] = useState('');
  const [statusText, setStatusText] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [source, setSource] = useState<StartupDefaultsSnapshot['source']>('fallback');
  const [operationMode, setOperationMode] = useState<StartupDefaultsSnapshot['operationMode']>(snapshot.operationMode);
  const [visionMode, setVisionMode] = useState<StartupDefaultsSnapshot['visionMode']>(snapshot.visionMode);

  useEffect(() => {
    let cancelled = false;
    const loadDefaults = async (): Promise<void> => {
      try {
        const result = await window.pixelPilot.getStartupDefaults();
        if (cancelled) {
          return;
        }
        setOperationMode(result.operationMode);
        setVisionMode(result.visionMode);
        setSource(result.source);
        setStatusText(result.hasPersisted ? 'Saved defaults will be applied on startup.' : startupDefaultsSourceLabel(result.source));
      } catch (error) {
        if (!cancelled) {
          setLocalError(error instanceof Error ? error.message : 'Unable to load startup defaults.');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void loadDefaults();
    return () => {
      cancelled = true;
    };
  }, []);

  const saveDefaults = async (): Promise<void> => {
    if (saving || disabled) {
      return;
    }
    setSaving(true);
    setLocalError('');
    setStatusText('Saving startup defaults...');
    try {
      const result = await window.pixelPilot.setStartupDefaults({ operationMode, visionMode });
      setSource(result.source);
      setStatusText('Startup defaults saved. They will be applied the next time PixelPilot starts.');
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to save startup defaults right now.');
      setStatusText('');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <div className="text-sm font-semibold text-slate-900">Startup Defaults</div>
        <div className="mt-1 text-sm text-slate-600">
          Save the mode and vision defaults PixelPilot should apply on the next launch.
        </div>
      </div>

      {loading ? (
        <div className="rounded-[18px] border border-white/38 bg-white/48 px-4 py-5 text-sm text-slate-500">
          Loading startup defaults...
        </div>
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-[18px] border border-white/38 bg-white/46 p-3">
              <div className="px-1 pb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">
                Mode On Startup
              </div>
              {modes.map((mode) => (
                <MenuItemButton
                  key={`startup-${mode.id}`}
                  label={mode.label}
                  active={operationMode === mode.id}
                  disabled={disabled || saving}
                  onClick={() => setOperationMode(mode.id)}
                />
              ))}
            </div>

            <div className="rounded-[18px] border border-white/38 bg-white/46 p-3">
              <div className="px-1 pb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">
                Vision On Startup
              </div>
              {visions.map((vision) => (
                <MenuItemButton
                  key={`startup-vision-${vision.id}`}
                  label={vision.label}
                  active={visionMode === vision.id}
                  disabled={disabled || saving}
                  onClick={() => setVisionMode(vision.id)}
                />
              ))}
            </div>
          </div>

          <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Status</div>
            <div className="mt-2 text-sm text-slate-700">
              {statusText || startupDefaultsSourceLabel(source)}
            </div>
          </div>

          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => void saveDefaults()}
              disabled={disabled || saving}
              className="no-drag rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-45"
            >
              {saving ? 'Saving...' : 'Save Startup Defaults'}
            </button>
          </div>
        </>
      )}

      {localError && (
        <div className="rounded-[18px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          {localError}
        </div>
      )}
    </div>
  );
}

function AuthGate({
  auth,
  runtimeError,
  onStartBrowserFlow,
  onExchangeCode,
  onUseApiKey,
  onQuit
}: {
  auth: AuthState;
  runtimeError: string;
  onStartBrowserFlow: (mode: 'signin' | 'signup') => Promise<void>;
  onExchangeCode: (code: string) => Promise<void>;
  onUseApiKey: (apiKey: string) => Promise<void>;
  onQuit: () => Promise<void>;
}): React.JSX.Element {
  const cardRef = useRef<HTMLDivElement | null>(null);
  const [browserCode, setBrowserCode] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState('');
  const [statusText, setStatusText] = useState('');
  const [statusIsError, setStatusIsError] = useState(false);
  useMeasuredWindowLayout(cardRef, {
    width: 420,
    height: 940
  });

  const startBrowser = async (mode: 'signin' | 'signup') => {
    setSubmitting(true);
    setLocalError('');
    setStatusText(mode === 'signup' ? 'Opening browser for account creation...' : 'Opening browser for sign-in...');
    setStatusIsError(false);
    try {
      await onStartBrowserFlow(mode);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Browser sign-in failed.');
      setStatusText('');
    } finally {
      setSubmitting(false);
    }
  };

  const submitBrowserCode = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!browserCode.trim()) {
      setLocalError('');
      setStatusText('Please enter the browser code');
      setStatusIsError(true);
      return;
    }
    setSubmitting(true);
    setLocalError('');
    setStatusText('Completing sign-in...');
    setStatusIsError(false);
    try {
      await onExchangeCode(browserCode);
      setBrowserCode('');
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Code exchange failed.');
      setStatusText('');
    } finally {
      setSubmitting(false);
    }
  };

  const submitApiKey = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!apiKey.trim()) {
      setLocalError('');
      setStatusText('Please enter an API Key');
      setStatusIsError(true);
      return;
    }
    if (!apiKey.trim().startsWith('AIza')) {
      setLocalError('');
      setStatusText('Invalid API Key format (should start with AIza)');
      setStatusIsError(true);
      return;
    }
    setSubmitting(true);
    setLocalError('');
    setStatusText('Verifying key...');
    setStatusIsError(false);
    try {
      await onUseApiKey(apiKey);
      setApiKey('');
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'API key setup failed.');
      setStatusText('');
    } finally {
      setSubmitting(false);
    }
  };

  const requestQuit = async (): Promise<void> => {
    setLocalError('');
    setStatusText('');
    try {
      await onQuit();
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Quit failed.');
    }
  };

  return (
    <motion.div
      className="mx-auto w-[420px]"
      initial={{ opacity: 0, y: -12 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div
        ref={cardRef}
        className="drag-region relative overflow-hidden rounded-2xl border border-[rgba(52,78,102,0.72)] bg-[rgba(18,30,44,0.96)] px-8 pb-8 pt-7 shadow-[0_24px_70px_rgba(3,10,18,0.45)]"
      >
        <button
          type="button"
          aria-label="Close login dialog"
          onClick={() => void requestQuit()}
          className="no-drag absolute right-3 top-3 flex h-7 w-7 items-center justify-center rounded-full text-[18px] font-bold text-[rgba(207,233,255,0.4)] transition hover:bg-[rgba(255,107,107,0.2)] hover:text-[#ff6b6b]"
        >
          <X className="h-4 w-4" />
        </button>

        <div className="flex justify-center">
          <div className="flex h-[50px] w-[50px] items-center justify-center">
            <PixelPilotLogo className="h-[50px] w-[50px]" />
          </div>
        </div>

        <div className="mt-4 text-center">
          <h1 className="text-[22px] font-bold tracking-[0.01em] text-[#cfe9ff]">Welcome Back</h1>
          <p className="mx-auto mt-3 max-w-[270px] text-[12px] leading-5 text-[rgba(207,233,255,0.6)]">
            Sign in or create your account in the browser, then return here automatically.
          </p>
        </div>

        <div className="mt-8 grid gap-3">
          <button
            type="button"
            disabled={submitting}
            onClick={() => void startBrowser('signin')}
            className="no-drag mt-6 min-h-[44px] rounded-[10px] border border-[#5fa6e8] bg-[#3e80c4] px-4 py-3 text-[13px] font-bold tracking-[0.04em] text-white transition hover:bg-[#4a8fd7] disabled:opacity-45"
          >
            {submitting ? 'Opening Browser...' : 'Sign In In Browser'}
          </button>

          <button
            type="button"
            disabled={submitting}
            onClick={() => void startBrowser('signup')}
            className="no-drag min-h-[44px] rounded-[10px] border border-[rgba(52,78,102,0.72)] bg-transparent px-4 py-3 text-[12px] font-semibold text-[#cfe9ff] transition hover:border-[#057FCA] hover:bg-[rgba(52,78,102,0.32)] disabled:opacity-45"
          >
            Create Account In Browser
          </button>
        </div>

        <div className="mt-7 text-center text-[11px] text-[rgba(207,233,255,0.5)]">
          If the browser does not return here automatically, paste the one-time browser code.
        </div>

        <form className="mt-3 grid gap-3" onSubmit={(event) => void submitBrowserCode(event)}>
          <input
            type="text"
            value={browserCode}
            onChange={(event) => setBrowserCode(event.target.value)}
            placeholder="Enter browser code"
            className="no-drag min-h-[42px] rounded-[10px] border border-[rgba(52,78,102,0.72)] bg-[rgba(20,36,54,0.78)] px-3.5 py-2.5 text-[13px] text-[#e5f3ff] outline-none transition placeholder:text-[rgba(207,233,255,0.4)] focus:border-[#057FCA]"
          />
          <button
            type="submit"
            disabled={submitting}
            className="no-drag min-h-[44px] rounded-[10px] border border-[rgba(52,78,102,0.72)] bg-transparent px-4 py-3 text-[12px] font-semibold text-[#cfe9ff] transition hover:border-[#057FCA] hover:bg-[rgba(52,78,102,0.32)] disabled:opacity-45"
          >
            {submitting ? 'Completing Sign-In...' : 'Continue With Browser Code'}
          </button>
        </form>

        <div className="mt-8 text-center text-[11px] text-[rgba(207,233,255,0.5)]">
          Or use your own Gemini API key for direct mode
        </div>

        <form className="mt-3 grid gap-3" onSubmit={(event) => void submitApiKey(event)}>
          <input
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="Paste Gemini API key (starts with AIza...)"
            className="no-drag min-h-[42px] rounded-[10px] border border-[rgba(52,78,102,0.72)] bg-[rgba(20,36,54,0.78)] px-3.5 py-2.5 text-[13px] text-[#e5f3ff] outline-none transition placeholder:text-[rgba(207,233,255,0.4)] focus:border-[#057FCA]"
          />
          <button
            type="submit"
            disabled={submitting}
            className="no-drag min-h-[44px] rounded-[10px] border border-[rgba(52,78,102,0.72)] bg-transparent px-4 py-3 text-[12px] font-semibold text-[#cfe9ff] transition hover:border-[#057FCA] hover:bg-[rgba(52,78,102,0.32)] disabled:opacity-45"
          >
            {submitting ? 'Verifying key...' : 'Use API Key'}
          </button>
        </form>

        <div className="mt-4 min-h-[20px] text-center text-[11px]">
          <span
            className={
              localError || runtimeError || statusIsError ? 'text-[#ff6b6b]' : 'text-[rgba(207,233,255,0.6)]'
            }
          >
            {localError || runtimeError || statusText}
          </span>
        </div>
      </div>
    </motion.div>
  );
}

function ConfirmationModal({
  request,
  onResolve
}: {
  request: RendererConfirmationRequest | null;
  onResolve: (approved: boolean) => Promise<void>;
}): React.JSX.Element | null {
  if (!request) {
    return null;
  }
  return (
    <AnimatePresence>
      <motion.div
        className="absolute inset-0 z-20 flex items-center justify-center rounded-[32px] bg-slate-950/18 p-4 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.96, y: 10 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.96, y: 10 }}
          className="w-full max-w-[360px]"
        >
          <GlassPanel className="p-4">
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Confirmation</div>
            <h2 className="mt-2 text-lg font-semibold text-slate-950">{request.title}</h2>
            <p className="mt-2 text-sm text-slate-700">{request.text}</p>
            <div className="mt-4 flex items-center justify-end gap-2">
              <SegmentedButton label="Cancel" onClick={() => void onResolve(false)} />
              <button
                type="button"
                onClick={() => void onResolve(true)}
                className="no-drag rounded-2xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
              >
                Approve
              </button>
            </div>
          </GlassPanel>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}

function LegacyDetailsPanel({
  snapshot,
  messages,
  updates,
  runtimeError,
  onStop
}: {
  snapshot: RuntimeSnapshot;
  messages: MessageEntry[];
  updates: ActionUpdate[];
  runtimeError: string;
  onStop: () => Promise<void>;
}): React.JSX.Element {
  const deferredMessages = useDeferredValue(messages);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [thinkingExpanded, setThinkingExpanded] = useState(false);
  const visibleMessages = useMemo(
    () =>
      deferredMessages
        .filter((entry) => {
          const kind = String(entry.kind || '').trim().toLowerCase();
          const speaker = String(entry.speaker || '').trim().toLowerCase();
          return kind === 'user' || kind === 'assistant' || speaker === 'user' || speaker === 'assistant';
        })
        .slice(-12),
    [deferredMessages]
  );
  const thinkingState = useMemo(() => buildThinkingState(snapshot, updates), [snapshot, updates]);
  const summaryLine = [
    snapshot.operationMode,
    snapshot.visionMode,
    snapshot.workspace === 'agent' ? 'Agent desktop' : 'User desktop',
    snapshot.liveAvailable ? humanizeState(snapshot.liveSessionState) : 'Offline',
    snapshot.wakeWordEnabled ? 'Wake word enabled' : 'Wake word disabled'
  ].join(' / ');

  useEffect(() => {
    if (!thinkingState.summary) {
      setThinkingExpanded(false);
    }
  }, [thinkingState.summary]);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) {
      return;
    }
    container.scrollTop = container.scrollHeight;
  }, [visibleMessages, thinkingState.summary, thinkingState.lines.length, thinkingExpanded]);
  const onStopRef = onStop;
  void onStopRef;

  return (
    <SoftPanel className="no-drag overflow-hidden p-0">
      <div className="border-b border-white/26 px-4 py-3">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Process details</div>
          <div className="mt-1 truncate text-xs text-slate-600">{summaryLine}</div>
        </div>
      </div>

      <div className="px-4 py-3">
        {!snapshot.liveAvailable && snapshot.liveUnavailableReason && (
          <div className="mb-3 rounded-2xl border border-amber-200/80 bg-amber-50/80 px-3 py-2.5 text-sm text-amber-900">
            {snapshot.liveUnavailableReason}
          </div>
        )}

        {runtimeError && (
          <div className="mb-3 rounded-2xl border border-rose-200/80 bg-rose-50/80 px-3 py-2.5 text-sm text-rose-900">
            {runtimeError}
          </div>
        )}

        <div className="overflow-hidden rounded-[20px] border border-white/32 bg-white/18">
          <div ref={scrollRef} className="no-drag max-h-[320px] min-h-[250px] overflow-y-auto px-3 py-3">
            {visibleMessages.length > 0 || thinkingState.summary ? (
              <div className="space-y-3">
                {visibleMessages.map((entry) => (
                  <div key={entry.id} className={messageBubbleClass(entry)}>
                    <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                      {speakerLabel(entry)}
                    </div>
                    <div>{entry.text}</div>
                    {!entry.final && <div className="mt-0.5 text-[11px] text-slate-400">Streaming...</div>}
                  </div>
                ))}
                {thinkingState.summary && (
                  <div className="max-w-[82%] px-1 py-0.5">
                    <button
                      type="button"
                      aria-expanded={thinkingExpanded}
                      onClick={() => setThinkingExpanded((current) => !current)}
                      className="no-drag flex w-full items-start gap-2 rounded-xl px-2 py-1.5 text-left transition hover:bg-white/18"
                    >
                      <span className="pt-0.5 font-mono text-[12px] text-slate-500">thinking&gt;</span>
                      <span className="min-w-0 flex-1 text-[13px] italic leading-6 text-slate-500">
                        {thinkingState.summary}
                      </span>
                      {thinkingExpanded ? (
                        <ChevronUp className="mt-1 h-3.5 w-3.5 shrink-0 text-slate-400" />
                      ) : (
                        <ChevronDown className="mt-1 h-3.5 w-3.5 shrink-0 text-slate-400" />
                      )}
                    </button>
                    <AnimatePresence initial={false}>
                      {thinkingExpanded && thinkingState.lines.length > 0 && (
                        <motion.div
                          initial={{ opacity: 0, y: -4 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, y: -4 }}
                          className="ml-4 mt-1 space-y-1 border-l border-white/24 pl-3"
                        >
                          {thinkingState.lines.map((line, index) => (
                            <div key={`${line}-${index}`} className="text-[12px] leading-5 text-slate-500">
                              {line}
                            </div>
                          ))}
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex min-h-[220px] items-end text-sm text-slate-500">
                Open apps, send emails/WhatsApp, fix PC issues, or ask anything...
              </div>
            )}
          </div>
        </div>
      </div>
    </SoftPanel>
  );
}

function OverlayShell({
  snapshot,
  messages,
  actionUpdates,
  confirmationRequest,
  runtimeError,
  onResolveConfirmation
}: {
  snapshot: RuntimeSnapshot;
  messages: MessageEntry[];
  actionUpdates: ActionUpdate[];
  confirmationRequest: RendererConfirmationRequest | null;
  runtimeError: string;
  onResolveConfirmation: (approved: boolean) => Promise<void>;
}): React.JSX.Element {
  const [commandText, setCommandText] = useState('');
  const [busyText, setBusyText] = useState('');
  const [localError, setLocalError] = useState('');
  const [pendingLiveToggle, setPendingLiveToggle] = useState(false);
  const [pendingVoiceToggle, setPendingVoiceToggle] = useState(false);
  const [optimisticLiveVoiceActive, setOptimisticLiveVoiceActive] = useState<boolean | null>(null);
  const submittingRef = useRef(false);
  const shellRef = useRef<HTMLDivElement>(null);
  const effectiveSnapshot = useMemo(
    () => ({
      ...snapshot,
      liveVoiceActive: optimisticLiveVoiceActive ?? snapshot.liveVoiceActive
    }),
    [optimisticLiveVoiceActive, snapshot]
  );
  const isBarOnly = !effectiveSnapshot.expanded;
  const bridgeBusy = isBridgeBusyStatus(effectiveSnapshot.bridgeStatus);
  const commandBarStatus = useMemo(
    () =>
      buildCommandBarStatus(
        effectiveSnapshot,
        messages,
        actionUpdates,
        busyText,
        localError,
        runtimeError
      ),
    [effectiveSnapshot, messages, actionUpdates, busyText, localError, runtimeError]
  );
  const showInlineStatus =
    commandBarStatus.kind === 'busy' || commandBarStatus.kind === 'error' || commandText.length === 0;
  const inlineStatusOverridesInput =
    commandBarStatus.kind === 'busy' || commandBarStatus.kind === 'error';
  const showInlineStop =
    effectiveSnapshot.liveAvailable && hasOngoingLiveTurn(effectiveSnapshot, actionUpdates, busyText);
  const inlineStatusToneClass =
    commandBarStatus.tone === 'error'
      ? 'text-rose-600'
      : commandBarStatus.tone === 'placeholder'
        ? 'text-slate-500'
        : 'text-slate-600';

  useEffect(() => {
    if (optimisticLiveVoiceActive !== null && snapshot.liveVoiceActive === optimisticLiveVoiceActive) {
      setOptimisticLiveVoiceActive(null);
      setPendingVoiceToggle(false);
    }
  }, [optimisticLiveVoiceActive, snapshot.liveVoiceActive]);

  const fallbackLayout = {
    width: 920,
    height: (isBarOnly ? 88 : 114) + (effectiveSnapshot.expanded ? 292 : 0)
  };
  useMeasuredWindowLayout(shellRef, fallbackLayout, [
    isBarOnly,
    effectiveSnapshot.expanded,
    effectiveSnapshot.liveVoiceActive,
    effectiveSnapshot.liveAvailable,
    effectiveSnapshot.operationMode,
    effectiveSnapshot.visionMode,
    effectiveSnapshot.workspace,
    effectiveSnapshot.agentViewRequested,
    effectiveSnapshot.userAudioLevel,
    effectiveSnapshot.assistantAudioLevel,
    messages.length,
    actionUpdates.length
  ]);

  const invoke = async (method: string, payload?: Record<string, unknown>): Promise<void> => {
    setLocalError('');
    try {
      await window.pixelPilot.invokeRuntime(method, payload);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Action could not be completed right now.');
    }
  };

  const submitCommand = async (): Promise<void> => {
    const text = commandText.trim();
    if (!text || submittingRef.current || bridgeBusy) {
      return;
    }
    submittingRef.current = true;
    setBusyText('Submitting...');
    try {
      await window.pixelPilot.invokeRuntime('live.submitText', { text });
      setCommandText('');
      setBusyText('');
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Failed to submit command.');
      setBusyText('');
    } finally {
      submittingRef.current = false;
    }
  };

  const toggleAgentViewFromBar = async (): Promise<void> => {
    if (!effectiveSnapshot.agentViewEnabled || bridgeBusy) {
      return;
    }

    const requested = !effectiveSnapshot.agentViewRequested;
    setBusyText(requested ? 'Showing agent preview...' : 'Hiding agent preview...');
    setLocalError('');
    try {
      await window.pixelPilot.invokeRuntime('agentView.setRequested', { requested });
      setBusyText('');
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to update the agent preview right now.');
      setBusyText('');
    }
  };

  const openSettingsMenu = async (): Promise<void> => {
    if (bridgeBusy) {
      return;
    }
    setLocalError('');
    try {
      await window.pixelPilot.toggleSettingsWindow();
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to open settings right now.');
    }
  };

  const toggleLiveMode = async (): Promise<void> => {
    if (pendingLiveToggle || bridgeBusy) {
      return;
    }
    if (!effectiveSnapshot.liveAvailable) {
      setLocalError(effectiveSnapshot.liveUnavailableReason || 'PixelPilot Live is unavailable right now.');
      return;
    }

    const liveState = String(effectiveSnapshot.liveSessionState || '').trim().toLowerCase();
    const requestReconnect = liveState === 'disconnected';
    setLocalError('');
    setBusyText(requestReconnect ? 'Reconnecting AI session...' : 'Disconnecting AI session...');
    setPendingLiveToggle(true);
    if (!requestReconnect) {
      setOptimisticLiveVoiceActive(false);
    }

    try {
      await window.pixelPilot.invokeRuntime('live.setEnabled', { enabled: requestReconnect });
      if (!requestReconnect) {
        setOptimisticLiveVoiceActive(false);
        setPendingVoiceToggle(false);
      }
      setPendingLiveToggle(false);
      setBusyText('');
    } catch (error) {
      setOptimisticLiveVoiceActive(null);
      setPendingLiveToggle(false);
      setBusyText('');
      setLocalError(error instanceof Error ? error.message : 'Unable to change the Live connection right now.');
    }
  };

  const toggleVoiceInput = async (): Promise<void> => {
    if (pendingVoiceToggle || pendingLiveToggle || bridgeBusy) {
      return;
    }
    if (!effectiveSnapshot.liveAvailable) {
      setLocalError(effectiveSnapshot.liveUnavailableReason || 'PixelPilot Live is unavailable right now.');
      return;
    }

    const liveState = String(effectiveSnapshot.liveSessionState || '').trim().toLowerCase();
    const startingFromDisconnected = liveState === 'disconnected' && !effectiveSnapshot.liveVoiceActive;
    const nextVoiceState = startingFromDisconnected ? true : !effectiveSnapshot.liveVoiceActive;
    setLocalError('');
    setBusyText(
      nextVoiceState
        ? startingFromDisconnected
          ? 'Reconnecting and starting voice...'
          : 'Starting voice input...'
        : 'Stopping voice input...'
    );
    setPendingVoiceToggle(true);

    try {
      if (startingFromDisconnected) {
        setPendingLiveToggle(true);
        const liveResult = await window.pixelPilot.invokeRuntime('live.setEnabled', { enabled: true });
        setPendingLiveToggle(false);
        const sessionState = String(liveResult.liveSessionState || '').trim().toLowerCase();
        if (sessionState === 'disconnected') {
          throw new Error('PixelPilot could not reconnect right now.');
        }
      }

      setOptimisticLiveVoiceActive(nextVoiceState);
      const payload = await window.pixelPilot.invokeRuntime('live.setVoice', { enabled: nextVoiceState });
      if (typeof payload.liveVoiceActive === 'boolean') {
        setOptimisticLiveVoiceActive(Boolean(payload.liveVoiceActive));
      }
      setPendingVoiceToggle(false);
      setBusyText('');
    } catch (error) {
      setOptimisticLiveVoiceActive(null);
      setPendingLiveToggle(false);
      setPendingVoiceToggle(false);
      setBusyText('');
      setLocalError(error instanceof Error ? error.message : 'Unable to change voice input right now.');
    }
  };

  const changeExpanded = async (expanded: boolean): Promise<void> => {
    if (expanded === effectiveSnapshot.expanded) {
      return;
    }
    setLocalError('');
    try {
      await window.pixelPilot.setExpanded(expanded);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to change the panel size right now.');
    }
  };

  const hideToNotch = async (): Promise<void> => {
    setLocalError('');
    try {
      await window.pixelPilot.setBackgroundHidden(true);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to hide PixelPilot right now.');
    }
  };

  const quitPixelPilot = async (): Promise<void> => {
    setLocalError('');
    try {
      await window.pixelPilot.quitApp();
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to quit PixelPilot right now.');
    }
  };

  const commandComposer = (
    <SoftPanel className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-2">
      <div className="relative min-w-0 flex-1">
        {showInlineStatus && (
          <span
            aria-hidden="true"
            className={`pointer-events-none absolute inset-y-0 left-0 right-0 flex items-center overflow-hidden ${inlineStatusToneClass}`}
          >
            <span className="block truncate whitespace-nowrap">{commandBarStatus.text}</span>
          </span>
        )}
        <input
          value={commandText}
          onChange={(event) => setCommandText(event.target.value)}
          readOnly={!effectiveSnapshot.liveAvailable || bridgeBusy}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              void submitCommand();
            }
          }}
          placeholder={inputPlaceholder(effectiveSnapshot)}
          className={[
            'no-drag relative z-10 block w-full border-0 bg-transparent text-[15px] outline-none',
            inlineStatusOverridesInput ? 'text-transparent caret-slate-800' : 'text-slate-800',
            showInlineStatus ? 'placeholder:text-transparent' : 'placeholder:text-slate-500'
          ].join(' ')}
        />
      </div>
      <MicControlButton
        snapshot={effectiveSnapshot}
        pending={pendingVoiceToggle}
        compact
        disabled={bridgeBusy}
        onClick={() => void toggleVoiceInput()}
      />
      {showInlineStop && (
        <button
          type="button"
          aria-label="Stop current turn"
          title="Stop current turn"
          onClick={() => void invoke('live.stop')}
          disabled={bridgeBusy}
          className="no-drag flex h-8 items-center justify-center rounded-xl border border-white/36 bg-white/34 px-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-700 transition hover:bg-white/48"
        >
          Stop
        </button>
      )}
      <button
        type="button"
        aria-label="Send command"
        onClick={() => void submitCommand()}
        disabled={!effectiveSnapshot.liveAvailable || bridgeBusy || !commandText.trim()}
        className="no-drag flex h-8 min-w-[38px] items-center justify-center rounded-xl bg-slate-900 px-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-white transition hover:bg-slate-800 disabled:opacity-40"
      >
        Go
      </button>
    </SoftPanel>
  );

  return (
    <motion.div
      ref={shellRef}
      className="relative mx-auto w-full max-w-[940px]"
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <GlassPanel className="drag-region relative overflow-hidden p-3">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(191,219,254,0.32),transparent_40%),radial-gradient(circle_at_bottom_right,rgba(226,232,240,0.48),transparent_52%)]" />
        <div className="relative">
          {isBarOnly ? (
            <div className="flex items-center gap-2">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[18px] border border-white/42 bg-white/22 shadow-[inset_0_1px_0_rgba(255,255,255,0.44)]">
                <PixelPilotLogo className="h-5 w-5" />
              </div>

              {commandComposer}

                <IconButton
                  compact
                  label={workspaceBadgeLabel(effectiveSnapshot)}
                  active={effectiveSnapshot.workspace === 'agent' && effectiveSnapshot.agentViewRequested}
                  disabled={!effectiveSnapshot.agentViewEnabled || bridgeBusy}
                  onClick={() => void toggleAgentViewFromBar()}
                >
                  {workspaceIcon(effectiveSnapshot.workspace)}
                </IconButton>
              <LiveModeButton
                snapshot={effectiveSnapshot}
                pending={pendingLiveToggle}
                compact
                disabled={bridgeBusy}
                onClick={() => void toggleLiveMode()}
              />
              <IconButton compact label="Open settings menu" disabled={bridgeBusy} onClick={() => void openSettingsMenu()}>
                <Settings2 className="h-4 w-4" />
              </IconButton>
              <IconButton compact label="Hide to notch" onClick={() => void hideToNotch()}>
                <Minus className="h-4 w-4" />
              </IconButton>
              <IconButton compact label="Expand details" onClick={() => void changeExpanded(true)}>
                <ChevronDown className="h-4 w-4" />
              </IconButton>
              <IconButton compact label="Quit PixelPilot" onClick={() => void quitPixelPilot()}>
                <X className="h-4 w-4" />
              </IconButton>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[18px] border border-white/42 bg-white/22 shadow-[inset_0_1px_0_rgba(255,255,255,0.44)]">
                <PixelPilotLogo className="h-5 w-5" />
              </div>

              {commandComposer}

                <IconButton
                  compact
                  label={workspaceBadgeLabel(effectiveSnapshot)}
                  active={effectiveSnapshot.workspace === 'agent' && effectiveSnapshot.agentViewRequested}
                  disabled={!effectiveSnapshot.agentViewEnabled || bridgeBusy}
                  onClick={() => void toggleAgentViewFromBar()}
                >
                  {workspaceIcon(effectiveSnapshot.workspace)}
                </IconButton>
              <LiveModeButton
                snapshot={effectiveSnapshot}
                pending={pendingLiveToggle}
                compact
                disabled={bridgeBusy}
                onClick={() => void toggleLiveMode()}
              />
              <IconButton compact label="Open settings menu" disabled={bridgeBusy} onClick={() => void openSettingsMenu()}>
                <Settings2 className="h-4 w-4" />
              </IconButton>
              <IconButton compact label="Hide to notch" onClick={() => void hideToNotch()}>
                <Minus className="h-4 w-4" />
              </IconButton>
              <IconButton compact label="Collapse details" active onClick={() => void changeExpanded(false)}>
                <ChevronUp className="h-4 w-4" />
              </IconButton>
              <IconButton compact label="Quit PixelPilot" onClick={() => void quitPixelPilot()}>
                <X className="h-4 w-4" />
              </IconButton>
            </div>
          )}

          <AnimatePresence initial={false}>
            {effectiveSnapshot.expanded && (
              <motion.div
                className="mt-3"
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
              >
                <LegacyDetailsPanel
                  snapshot={effectiveSnapshot}
                  messages={messages}
                  updates={actionUpdates}
                  runtimeError={runtimeError}
                  onStop={() => invoke('live.stop')}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <ConfirmationModal request={confirmationRequest} onResolve={onResolveConfirmation} />
      </GlassPanel>
    </motion.div>
  );
}

function SessionSettingsSection({
  snapshot,
  disabled = false
}: {
  snapshot: RuntimeSnapshot;
  disabled?: boolean;
}): React.JSX.Element {
  const [localError, setLocalError] = useState('');
  const [busyAction, setBusyAction] = useState('');
  const [sessionContext, setSessionContext] = useState<SessionContextSummary>(
    parseSessionContext(snapshot.latestSessionContext)
  );
  const sessionDirectory = String(snapshot.sessionDirectory || '').trim();

  useEffect(() => {
    setSessionContext(parseSessionContext(snapshot.latestSessionContext));
  }, [snapshot.latestSessionContext]);

  useEffect(() => {
    let cancelled = false;

    const loadLatestContext = async (): Promise<void> => {
      try {
        const result = await window.pixelPilot.invokeRuntime('session.getLatestContext');
        if (!cancelled) {
          setSessionContext(parseSessionContext(result.session));
        }
      } catch (error) {
        if (!cancelled) {
          setLocalError(error instanceof Error ? error.message : 'Unable to load the latest session context.');
        }
      }
    };

    void loadLatestContext();
    return () => {
      cancelled = true;
    };
  }, []);

  const resumeLastContext = async (): Promise<void> => {
    if (disabled) {
      return;
    }
    setBusyAction('resume');
    setLocalError('');
    try {
      const result = await window.pixelPilot.invokeRuntime('session.resumeLatestContext');
      setSessionContext(parseSessionContext(result.session));
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to resume the latest context right now.');
    } finally {
      setBusyAction('');
    }
  };

  const openSessionFolder = async (): Promise<void> => {
    if (disabled) {
      return;
    }
    setBusyAction('open-folder');
    setLocalError('');
    try {
      await window.pixelPilot.invokeRuntime('session.openFolder');
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to open the session folder right now.');
    } finally {
      setBusyAction('');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-slate-900">Sessions</div>
          <div className="mt-1 text-sm text-slate-600">Manual resume and session log access for the current workspace.</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void resumeLastContext()}
            disabled={!sessionContext.available || busyAction !== '' || disabled}
            className="no-drag rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {busyAction === 'resume' ? 'Resuming...' : 'Resume Last Context'}
          </button>
          <button
            type="button"
            onClick={() => void openSessionFolder()}
            disabled={!sessionDirectory || busyAction !== '' || disabled}
            className="no-drag rounded-xl border border-white/38 bg-white/46 px-4 py-2.5 text-sm font-semibold text-slate-800 transition hover:bg-white/62 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {busyAction === 'open-folder' ? 'Opening...' : 'Open Session Logs'}
          </button>
        </div>
      </div>

      <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Latest Summary</div>
        <div className="mt-2 text-sm leading-6 text-slate-700">
          {sessionContext.available
            ? sessionContext.summaryText || 'A resumable session exists, but no compact summary was stored yet.'
            : 'No resumable session context has been stored yet.'}
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Last Activity</div>
          <div className="mt-2 break-all text-sm text-slate-700">{sessionContext.lastActivityAt || 'Unknown'}</div>
        </div>
        <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Session ID</div>
          <div className="mt-2 break-all text-sm text-slate-700">{sessionContext.sessionId || 'Unavailable'}</div>
        </div>
      </div>

      <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Session Directory</div>
        <div className="mt-2 break-all text-sm text-slate-700">{sessionDirectory || 'Unavailable'}</div>
      </div>

      {localError && (
        <div className="rounded-[18px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          {localError}
        </div>
      )}
    </div>
  );
}

function ExtensionsSettingsSection({
  snapshot,
  disabled = false
}: {
  snapshot: RuntimeSnapshot;
  disabled?: boolean;
}): React.JSX.Element {
  const [localError, setLocalError] = useState('');
  const [busy, setBusy] = useState(false);
  const [extensionSummary, setExtensionSummary] = useState<ExtensionSummary>(
    parseExtensionSummary(snapshot.extensions)
  );

  useEffect(() => {
    setExtensionSummary(parseExtensionSummary(snapshot.extensions));
  }, [snapshot.extensions]);

  useEffect(() => {
    let cancelled = false;

    const loadSummary = async (): Promise<void> => {
      try {
        const result = await window.pixelPilot.invokeRuntime('extensions.getSummary');
        if (!cancelled) {
          setExtensionSummary(parseExtensionSummary(result.extensions));
        }
      } catch (error) {
        if (!cancelled) {
          setLocalError(error instanceof Error ? error.message : 'Unable to load extension state right now.');
        }
      }
    };

    void loadSummary();
    return () => {
      cancelled = true;
    };
  }, []);

  const reloadExtensions = async (): Promise<void> => {
    if (disabled) {
      return;
    }
    setBusy(true);
    setLocalError('');
    try {
      const result = await window.pixelPilot.invokeRuntime('extensions.reload');
      setExtensionSummary(parseExtensionSummary(result.extensions));
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to reload extensions right now.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-slate-900">Extensions</div>
          <div className="mt-1 text-sm text-slate-600">Plugin and MCP tool discovery with explicit local opt-in.</div>
        </div>
        <button
          type="button"
          onClick={() => void reloadExtensions()}
          disabled={busy || disabled}
          className="no-drag rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-45"
        >
          {busy ? 'Reloading...' : 'Reload Extensions'}
        </button>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Health</div>
          <div className="mt-2 text-sm text-slate-700">
            {extensionSummary.status === 'ready'
              ? extensionSummary.toolCount > 0
                ? 'Ready'
                : 'No tools loaded'
              : extensionSummary.status || 'Unknown'}
          </div>
        </div>
        <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Plugins</div>
          <div className="mt-2 text-sm text-slate-700">{extensionSummary.pluginCount}</div>
        </div>
        <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">MCP Servers</div>
          <div className="mt-2 text-sm text-slate-700">{extensionSummary.mcpServerCount}</div>
        </div>
      </div>

      <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Plugin IDs</div>
        <div className="mt-2 text-sm text-slate-700">
          {extensionSummary.pluginIds && extensionSummary.pluginIds.length > 0
            ? extensionSummary.pluginIds.join(', ')
            : 'No plugins loaded.'}
        </div>
      </div>

      <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">MCP Servers</div>
        <div className="mt-2 text-sm text-slate-700">
          {extensionSummary.mcpServerNames && extensionSummary.mcpServerNames.length > 0
            ? extensionSummary.mcpServerNames.join(', ')
            : 'No MCP servers configured.'}
        </div>
      </div>

      <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Tool Names</div>
        <div className="mt-2 max-h-[180px] overflow-y-auto text-sm leading-6 text-slate-700">
          {extensionSummary.toolNames.length > 0
            ? extensionSummary.toolNames.join(', ')
            : 'No extension tools are currently loaded.'}
        </div>
      </div>

      {localError && (
        <div className="rounded-[18px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          {localError}
        </div>
      )}
    </div>
  );
}

function GeneralSettingsSection({
  snapshot,
  disabled = false
}: {
  snapshot: RuntimeSnapshot;
  disabled?: boolean;
}): React.JSX.Element {
  const [localError, setLocalError] = useState('');

  const runAction = async (method: string, payload?: Record<string, unknown>): Promise<void> => {
    if (disabled) {
      return;
    }
    setLocalError('');
    try {
      await window.pixelPilot.invokeRuntime(method, payload);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to update settings right now.');
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <div className="text-sm font-semibold text-slate-900">General</div>
        <div className="mt-1 text-sm text-slate-600">Adjust the active mode, vision pipeline, and account state.</div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-[18px] border border-white/38 bg-white/46 p-3">
          <div className="px-1 pb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Mode</div>
          {modes.map((mode) => (
            <MenuItemButton
              key={`general-mode-${mode.id}`}
              label={mode.label}
              active={snapshot.operationMode === mode.id}
              disabled={disabled}
              onClick={() => void runAction('mode.set', { value: mode.id })}
            />
          ))}
        </div>

        <div className="rounded-[18px] border border-white/38 bg-white/46 p-3">
          <div className="px-1 pb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Vision</div>
          {visions.map((vision) => (
            <MenuItemButton
              key={`general-vision-${vision.id}`}
              label={vision.label}
              active={snapshot.visionMode === vision.id}
              disabled={disabled}
              onClick={() => void runAction('vision.set', { value: vision.id })}
            />
          ))}
        </div>
      </div>

      <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Settings Sources</div>
        <div className="mt-2 space-y-2 text-sm text-slate-700">
          {snapshot.settingsSources.length > 0 ? (
            snapshot.settingsSources.map((source) => (
              <div key={source} className="break-all rounded-xl border border-white/28 bg-white/54 px-3 py-2">
                {source}
              </div>
            ))
          ) : (
            <div>No JSON settings files are active right now.</div>
          )}
        </div>
      </div>

      <div className="flex justify-start">
        <button
          type="button"
          onClick={() => void runAction('auth.logout')}
          disabled={disabled}
          className="no-drag rounded-xl border border-white/38 bg-white/46 px-4 py-2.5 text-sm font-semibold text-slate-800 transition hover:bg-white/62 disabled:cursor-not-allowed disabled:opacity-45"
        >
          Sign Out
        </button>
      </div>

      {localError && (
        <div className="rounded-[18px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          {localError}
        </div>
      )}
    </div>
  );
}

function DiagnosticsSettingsSection({
  snapshot,
  disabled = false
}: {
  snapshot: RuntimeSnapshot;
  disabled?: boolean;
}): React.JSX.Element {
  const [localError, setLocalError] = useState('');
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<DoctorReport>(parseDoctorReport(snapshot.lastDoctorReport));
  const [doctorText, setDoctorText] = useState(() => renderDoctorReportText(parseDoctorReport(snapshot.lastDoctorReport)));

  useEffect(() => {
    const nextReport = parseDoctorReport(snapshot.lastDoctorReport);
    setReport(nextReport);
    setDoctorText((current) => current || renderDoctorReportText(nextReport));
  }, [snapshot.lastDoctorReport]);

  const runDiagnostics = async (): Promise<void> => {
    if (disabled) {
      return;
    }
    setBusy(true);
    setLocalError('');
    try {
      const result = await window.pixelPilot.invokeRuntime('doctor.run');
      const nextReport = parseDoctorReport(result.doctor);
      setReport(nextReport);
      setDoctorText(String(result.text || renderDoctorReportText(nextReport)));
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to run diagnostics right now.');
    } finally {
      setBusy(false);
    }
  };

  const copyDoctorText = async (): Promise<void> => {
    setLocalError('');
    try {
      await copyTextToClipboard(doctorText || renderDoctorReportText(report));
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to copy the doctor text right now.');
    }
  };

  const copyDoctorJson = async (): Promise<void> => {
    setLocalError('');
    try {
      await copyTextToClipboard(JSON.stringify(report, null, 2));
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to copy the doctor JSON right now.');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-slate-900">Diagnostics</div>
          <div className="mt-1 text-sm text-slate-600">Run the shared doctor pipeline and copy the latest runtime health report.</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void runDiagnostics()}
            disabled={busy || disabled}
            className="no-drag rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {busy ? 'Running...' : 'Run Diagnostics'}
          </button>
          <button
            type="button"
            onClick={() => void copyDoctorText()}
            disabled={busy}
            className="no-drag rounded-xl border border-white/38 bg-white/46 px-4 py-2.5 text-sm font-semibold text-slate-800 transition hover:bg-white/62 disabled:cursor-not-allowed disabled:opacity-45"
          >
            Copy Doctor Text
          </button>
          <button
            type="button"
            onClick={() => void copyDoctorJson()}
            disabled={busy}
            className="no-drag rounded-xl border border-white/38 bg-white/46 px-4 py-2.5 text-sm font-semibold text-slate-800 transition hover:bg-white/62 disabled:cursor-not-allowed disabled:opacity-45"
          >
            Copy Doctor JSON
          </button>
        </div>
      </div>

      <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Overall Status</div>
        <div className="mt-2 text-sm text-slate-700">{report.status || 'Unknown'}</div>
      </div>

      <div className="space-y-3">
        {report.checks.length > 0 ? (
          report.checks.map((check) => (
            <div key={check.name} className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-semibold text-slate-900">{check.name}</div>
                <StatusPill label={humanizeState(check.status)} active={check.status === 'ok'} />
              </div>
              <div className="mt-2 text-sm text-slate-700">{check.summary || 'No summary provided.'}</div>
            </div>
          ))
        ) : (
          <div className="rounded-[18px] border border-white/38 bg-white/46 px-4 py-5 text-sm text-slate-600">
            No diagnostics report has been captured yet. Run diagnostics to inspect runtime, wake word, audio, UAC, and extension health.
          </div>
        )}
      </div>

      {localError && (
        <div className="rounded-[18px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          {localError}
        </div>
      )}
    </div>
  );
}

function SettingsNavButton({
  label,
  active,
  onClick
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}): React.JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        'no-drag flex w-full items-center rounded-xl px-3 py-2.5 text-left text-sm font-medium transition',
        active ? 'bg-white/60 text-slate-900 shadow-[0_8px_18px_rgba(15,23,42,0.08)]' : 'text-slate-700 hover:bg-white/38'
      ].join(' ')}
    >
      {label}
    </button>
  );
}

function SettingsShell({ snapshot }: { snapshot: RuntimeSnapshot }): React.JSX.Element {
  const [activeSection, setActiveSection] = useState<SettingsSectionId>('general');
  const [localError, setLocalError] = useState('');
  const bridgeBusy = isBridgeBusyStatus(snapshot.bridgeStatus);
  const bridgeStateText = bridgeStatusText(snapshot);

  useWindowLayout({ width: 640, height: 560 });

  const closeWindow = async (): Promise<void> => {
    setLocalError('');
    try {
      await window.pixelPilot.closeSettingsWindow();
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to close settings right now.');
    }
  };

  const sections: { id: SettingsSectionId; label: string }[] = [
    { id: 'general', label: 'General' },
    { id: 'startup', label: 'Startup' },
    { id: 'sessions', label: 'Sessions' },
    { id: 'extensions', label: 'Extensions' },
    { id: 'diagnostics', label: 'Diagnostics' }
  ];

  return (
    <motion.div
      className="w-[640px]"
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className="overflow-hidden rounded-[28px] border border-white/45 bg-[rgba(236,245,255,0.82)] shadow-[0_24px_56px_rgba(15,23,42,0.18)] backdrop-blur-2xl">
        <div className="flex h-[560px] min-h-[560px]">
          <aside className="flex w-[176px] shrink-0 flex-col border-r border-white/30 bg-[rgba(255,255,255,0.56)] px-3 py-4">
            <div className="px-2">
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Settings Hub</div>
              <div className="mt-2 text-sm text-slate-700">Startup, sessions, extensions, and diagnostics now live in one place.</div>
            </div>
            <div className="mt-5 flex-1 space-y-1">
              {sections.map((section) => (
                <SettingsNavButton
                  key={section.id}
                  label={section.label}
                  active={activeSection === section.id}
                  onClick={() => setActiveSection(section.id)}
                />
              ))}
            </div>
            {bridgeStateText && snapshot.bridgeStatus !== 'connected' && (
              <div
                className={[
                  'mt-3 rounded-2xl border px-3 py-2 text-xs',
                  snapshot.bridgeStatus === 'failed'
                    ? 'border-rose-200 bg-rose-50 text-rose-900'
                    : 'border-sky-200 bg-sky-50 text-sky-900'
                ].join(' ')}
              >
                {bridgeStateText}
              </div>
            )}
          </aside>

          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex items-center justify-between gap-3 border-b border-white/28 px-5 py-4">
              <div>
                <div className="text-lg font-semibold text-slate-950">
                  {sections.find((section) => section.id === activeSection)?.label}
                </div>
                <div className="mt-1 text-sm text-slate-600">
                  {snapshot.bridgeStatus === 'connected' ? 'Runtime connected' : bridgeStateText}
                </div>
              </div>
              <button
                type="button"
                aria-label="Close settings hub"
                onClick={() => void closeWindow()}
                className="no-drag flex h-9 w-9 items-center justify-center rounded-full border border-white/34 bg-white/32 text-slate-700 transition hover:bg-white/48"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              {activeSection === 'general' && <GeneralSettingsSection snapshot={snapshot} disabled={bridgeBusy} />}
              {activeSection === 'startup' && <StartupDefaultsSection snapshot={snapshot} disabled={bridgeBusy} />}
              {activeSection === 'sessions' && <SessionSettingsSection snapshot={snapshot} disabled={bridgeBusy} />}
              {activeSection === 'extensions' && <ExtensionsSettingsSection snapshot={snapshot} disabled={bridgeBusy} />}
              {activeSection === 'diagnostics' && <DiagnosticsSettingsSection snapshot={snapshot} disabled={bridgeBusy} />}

              {localError && (
                <div className="mt-4 rounded-[18px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
                  {localError}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

function NotchShell({
  snapshot,
  actionUpdates,
  runtimeError
}: {
  snapshot: RuntimeSnapshot;
  actionUpdates: ActionUpdate[];
  runtimeError: string;
}): React.JSX.Element {
  const [localError, setLocalError] = useState('');
  const progress = buildNotchProgress(snapshot, actionUpdates, localError || runtimeError);
  useWindowLayout({ width: 420, height: 62 });

  const restoreOverlay = async (): Promise<void> => {
    setLocalError('');
    try {
      await window.pixelPilot.setBackgroundHidden(false);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to restore PixelPilot right now.');
    }
  };

  const runInTrayOnly = async (): Promise<void> => {
    setLocalError('');
    try {
      await window.pixelPilot.setTrayOnly(true);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to switch to tray-only mode right now.');
    }
  };

  return (
    <motion.div
      className="mx-auto w-[420px]"
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div
        className={[
          'drag-region no-drag rounded-b-[22px] border border-white/24 backdrop-blur-2xl',
          'bg-[rgba(236,245,255,0.12)] px-3 py-2.5',
          'shadow-[0_10px_34px_rgba(15,23,42,0.24)]'
        ].join(' ')}
      >
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="Run in tray only"
            title="Run in tray only"
            onClick={() => void runInTrayOnly()}
            className="no-drag flex h-6 w-6 items-center justify-center rounded-full border border-white/30 bg-white/12 text-slate-200 transition hover:bg-white/22"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>

          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium text-slate-900">{progress.text}</div>
          </div>

          <button
            type="button"
            aria-label="Restore overlay"
            title="Restore overlay"
            onClick={() => void restoreOverlay()}
            className="no-drag flex h-6 w-6 items-center justify-center rounded-full border border-cyan-200/70 bg-cyan-100/28 text-cyan-900 transition hover:bg-cyan-100/45"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="mt-2.5 h-[3px] overflow-hidden rounded-full bg-white/20">
          {progress.busy ? (
            <motion.div
              className="h-full w-[34%] rounded-full bg-cyan-300/90"
              animate={{ x: ['-120%', '260%'] }}
              transition={{ duration: 1.5, ease: 'linear', repeat: Number.POSITIVE_INFINITY }}
            />
          ) : (
            <div
              className={[
                'h-full rounded-full',
                progress.tone === 'error'
                  ? 'w-[28%] bg-rose-400/90'
                  : progress.tone === 'active'
                    ? 'w-[42%] bg-cyan-300/88'
                    : 'w-[16%] bg-slate-300/80'
              ].join(' ')}
            />
          )}
        </div>

        {localError && (
          <div className="mt-2 truncate text-[11px] text-rose-200">{localError}</div>
        )}
      </div>
    </motion.div>
  );
}

function SidecarShell({
  snapshot,
  frame
}: {
  snapshot: RuntimeSnapshot;
  frame: SidecarFrame | null;
}): React.JSX.Element {
  const [localError, setLocalError] = useState('');
  useWindowLayout({
    width: 404,
    height: frame ? 492 : 456
  });

  const toggleSidecar = async (): Promise<void> => {
    setLocalError('');
    try {
      await window.pixelPilot.invokeRuntime('agentView.setRequested', {
        requested: !snapshot.agentViewRequested
      });
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to update the sidecar right now.');
    }
  };

  const returnToBar = async (): Promise<void> => {
    setLocalError('');
    try {
      await window.pixelPilot.setBackgroundHidden(false);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to restore the main bar right now.');
    }
  };

  return (
    <motion.div
      className="mx-auto w-full max-w-[404px]"
      initial={{ opacity: 0, x: 12 }}
      animate={{ opacity: 1, x: 0 }}
    >
      <GlassPanel className="drag-region relative overflow-hidden p-3">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(191,219,254,0.28),transparent_40%),radial-gradient(circle_at_bottom_right,rgba(226,232,240,0.46),transparent_52%)]" />
        <div className="relative">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Agent desktop</div>
              <div className="mt-1 text-lg font-semibold text-slate-950">Isolated sidecar preview</div>
            </div>
            <StatusPill
              label={snapshot.sidecarVisible ? 'Streaming' : 'Waiting'}
              active={snapshot.sidecarVisible}
              icon={<Monitor className="h-3.5 w-3.5" />}
            />
          </div>

          <div className="mt-3 rounded-[24px] border border-white/42 bg-white/26 p-3 backdrop-blur-xl">
            <div className="mb-3 flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-slate-400/90" />
              <span className="h-2.5 w-2.5 rounded-full bg-slate-300/90" />
              <span className="h-2.5 w-2.5 rounded-full bg-slate-200/90" />
            </div>
            <div className="overflow-hidden rounded-[20px] border border-white/38 bg-slate-950/6">
              {frame ? (
                <img
                  src={frame.dataUrl}
                  alt="Agent desktop preview"
                  className="aspect-[16/10] w-full object-cover"
                />
              ) : (
                <div className="flex aspect-[16/10] w-full items-center justify-center px-6 text-center text-sm text-slate-600">
                  {snapshot.agentPreviewAvailable
                    ? 'Waiting for the next desktop preview frame.'
                    : 'Agent preview will appear once the workspace is ready and the desktop manager is attached.'}
                </div>
              )}
            </div>
          </div>

          <div className="mt-3 grid gap-2">
            <StatusPill
              label={snapshot.workspace === 'agent' ? 'Agent workspace' : 'User workspace'}
              active={snapshot.workspace === 'agent'}
              icon={<Monitor className="h-3.5 w-3.5" />}
            />
            <StatusPill
              label={snapshot.agentViewRequested ? 'Sidecar requested' : 'Sidecar hidden'}
              active={snapshot.agentViewRequested}
              icon={<Eye className="h-3.5 w-3.5" />}
            />
          </div>

          {localError && (
            <div className="mt-3 rounded-2xl border border-rose-200/80 bg-rose-50/80 px-3 py-2.5 text-sm text-rose-900">
              {localError}
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            <SegmentedButton
              label={snapshot.agentViewRequested ? 'Hide sidecar' : 'Show sidecar'}
              onClick={() => void toggleSidecar()}
              disabled={snapshot.workspace !== 'agent'}
              icon={<Monitor className="h-4 w-4" />}
            />
            <SegmentedButton
              label="Return to bar"
              onClick={() => void returnToBar()}
              icon={<PanelTop className="h-4 w-4" />}
            />
          </div>
        </div>
      </GlassPanel>
    </motion.div>
  );
}

function usePixelPilotModel(): {
  ready: boolean;
  windowKind: WindowKind | null;
  snapshot: RuntimeSnapshot | null;
  messages: MessageEntry[];
  actionUpdates: ActionUpdate[];
  sidecarFrame: SidecarFrame | null;
  confirmationRequest: RendererConfirmationRequest | null;
  runtimeError: string;
  resolveConfirmation: (approved: boolean) => Promise<void>;
} {
  const [ready, setReady] = useState(false);
  const [windowKind, setWindowKind] = useState<WindowKind | null>(null);
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [messages, setMessages] = useState<MessageEntry[]>([]);
  const [actionUpdates, setActionUpdates] = useState<ActionUpdate[]>([]);
  const [sidecarFrame, setSidecarFrame] = useState<SidecarFrame | null>(null);
  const [confirmationRequest, setConfirmationRequest] = useState<RendererConfirmationRequest | null>(null);
  const [runtimeError, setRuntimeError] = useState('');

  useEffect(() => {
    let disposed = false;

    const initialize = async (): Promise<void> => {
      try {
        const [kind, currentSnapshot] = await Promise.all([
          window.pixelPilot.getWindowKind(),
          window.pixelPilot.getSnapshot()
        ]);
        if (disposed) {
          return;
        }
        startTransition(() => {
          setWindowKind(kind);
          setReady(true);
          if (currentSnapshot) {
            setSnapshot(currentSnapshot);
            setMessages(currentSnapshot.recentMessages.map(normalizeEntry));
            setActionUpdates(currentSnapshot.recentActionUpdates);
          }
        });
      } catch (error) {
        if (disposed) {
          return;
        }
        startTransition(() => {
          setReady(true);
          setRuntimeError(error instanceof Error ? error.message : 'PixelPilot is still starting. Please wait a moment.');
        });
      }
    };

    void initialize();

    const offState = window.pixelPilot.onState((nextSnapshot) => {
      startTransition(() => {
        setRuntimeError('');
        setSnapshot(nextSnapshot);
        setMessages(nextSnapshot.recentMessages.map(normalizeEntry));
        setActionUpdates(nextSnapshot.recentActionUpdates);
      });
    });

    const offEvent = window.pixelPilot.onEvent((envelope) => {
      startTransition(() => {
        if (envelope.kind === 'error' || envelope.method === 'runtime.error') {
          setRuntimeError(String(envelope.payload.message || 'PixelPilot is temporarily unavailable.'));
          return;
        }

        if (envelope.method === 'message.appended') {
          const entry = normalizeEntry(envelope.payload.entry as MessageEntry);
          setMessages((current) => trimMessages([...current.filter((item) => item.id !== entry.id), entry]));
          return;
        }

        if (envelope.method === 'live.transcript') {
          setMessages((current) => mergeTranscript(current, envelope.payload));
          return;
        }

        if (envelope.method === 'live.actionState') {
          setActionUpdates((current) => mergeActionUpdate(current, envelope.payload));
          return;
        }

        if (envelope.method === 'auth.changed') {
          const auth = (envelope.payload.auth as AuthState | undefined) ?? emptyAuth;
          setSnapshot((current) => patchSnapshot(current, { auth }));
          return;
        }

        if (envelope.method === 'live.sessionState') {
          setSnapshot((current) =>
            patchSnapshot(current, {
              liveSessionState: String(envelope.payload.state || 'disconnected')
            })
          );
          return;
        }

        if (envelope.method === 'live.availability') {
          setSnapshot((current) =>
            patchSnapshot(current, {
              liveAvailable: Boolean(envelope.payload.available),
              liveUnavailableReason: String(envelope.payload.reason || '')
            })
          );
          return;
        }

        if (envelope.method === 'live.voiceActive') {
          setSnapshot((current) =>
            patchSnapshot(current, {
              liveVoiceActive: Boolean(envelope.payload.active)
            })
          );
          return;
        }

        if (envelope.method === 'live.audioLevel') {
          const level = clamp(Number(envelope.payload.level || 0));
          const channel = String(envelope.payload.channel || '');
          setSnapshot((current) =>
            patchSnapshot(
              current,
              channel === 'user' ? { userAudioLevel: level } : { assistantAudioLevel: level }
            )
          );
          return;
        }

        if (envelope.method === 'sidecar.visibility') {
          const visible = Boolean(envelope.payload.visible);
          setSnapshot((current) =>
            patchSnapshot(current, {
              sidecarVisible: visible
            })
          );
          if (!visible) {
            setSidecarFrame(null);
          }
        }
      });
    });

    const offConfirmation = window.pixelPilot.onConfirmationRequest((request) => {
      startTransition(() => {
        setConfirmationRequest(request);
      });
    });

    const offSidecar = window.pixelPilot.onSidecarFrame((frame) => {
      startTransition(() => {
        setSidecarFrame(frame);
      });
    });

    return () => {
      disposed = true;
      offState();
      offEvent();
      offConfirmation();
      offSidecar();
    };
  }, []);

  const resolveConfirmation = async (approved: boolean): Promise<void> => {
    if (!confirmationRequest) {
      return;
    }
    const requestId = confirmationRequest.id;
    setConfirmationRequest(null);
    await window.pixelPilot.resolveConfirmation(requestId, { approved });
  };

  return {
    ready,
    windowKind,
    snapshot,
    messages,
    actionUpdates,
    sidecarFrame,
    confirmationRequest,
    runtimeError,
    resolveConfirmation
  };
}

export default function App(): React.JSX.Element {
  const {
    ready,
    windowKind,
    snapshot,
    messages,
    actionUpdates,
    sidecarFrame,
    confirmationRequest,
    runtimeError,
    resolveConfirmation
  } = usePixelPilotModel();

  if (!ready) {
    return <LoadingShell windowKind={windowKind} />;
  }

  if (!snapshot) {
    return (
      <LoadingShell
        windowKind={windowKind}
        statusText={runtimeError || 'PixelPilot is starting. We will reconnect automatically.'}
      />
    );
  }

  const shellBody = snapshot.auth.needsAuth ? (
    <AuthGate
      auth={snapshot.auth}
      runtimeError={runtimeError}
      onStartBrowserFlow={async (mode) => {
        const result = await window.pixelPilot.invokeRuntime('auth.startBrowserFlow', { mode });
        const authUrl = String(result.authUrl || '').trim();
        if (!authUrl) {
          throw new Error('Browser auth URL is unavailable.');
        }
        await window.pixelPilot.openExternal(authUrl);
      }}
      onExchangeCode={(code) =>
        window.pixelPilot
          .invokeRuntime('auth.exchangeDesktopCode', {
            code
          })
          .then(() => undefined)
      }
      onUseApiKey={(apiKey) =>
        window.pixelPilot
          .invokeRuntime('auth.useApiKey', {
            apiKey
          })
          .then(() => undefined)
      }
      onQuit={() => window.pixelPilot.quitApp().then(() => undefined)}
    />
  ) : windowKind === 'notch' ? (
    <NotchShell snapshot={snapshot} actionUpdates={actionUpdates} runtimeError={runtimeError} />
  ) : windowKind === 'sidecar' ? (
    <SidecarShell snapshot={snapshot} frame={sidecarFrame} />
  ) : windowKind === 'settings' ? (
    <SettingsShell snapshot={snapshot} />
  ) : (
    <OverlayShell
      snapshot={snapshot}
      messages={messages}
      actionUpdates={actionUpdates}
      confirmationRequest={confirmationRequest}
      runtimeError={runtimeError}
      onResolveConfirmation={resolveConfirmation}
    />
  );

  return (
    <div
      className={[
        'relative w-full overflow-visible text-slate-900',
        windowKind === 'notch'
          ? 'flex items-start justify-center'
          : windowKind === 'sidecar'
            ? 'flex items-start justify-center p-3'
            : isSettingsWindowKind(windowKind)
              ? 'flex items-start justify-start'
            : 'flex items-start justify-center p-3'
      ].join(' ')}
    >
      {!isSettingsWindowKind(windowKind) && (
        <>
          <div className="pointer-events-none absolute left-[8%] top-[-18%] h-28 w-28 rounded-full bg-sky-200/18 blur-3xl" />
          <div className="pointer-events-none absolute bottom-[-20%] right-[10%] h-32 w-32 rounded-full bg-slate-300/18 blur-3xl" />
        </>
      )}
      {shellBody}
    </div>
  );
}
