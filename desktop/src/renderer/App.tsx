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
  Activity,
  Bot,
  ChevronDown,
  ChevronUp,
  Command,
  Eye,
  GraduationCap,
  Keyboard,
  LoaderCircle,
  LogOut,
  Mic,
  Minus,
  Monitor,
  PanelTop,
  ScanSearch,
  Send,
  Settings2,
  Shield,
  Waves,
  X
} from 'lucide-react';
import type {
  ActionUpdate,
  AuthState,
  MessageEntry,
  RendererConfirmationRequest,
  RuntimeSnapshot,
  SidecarFrame,
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

type WindowLayout = {
  width: number;
  height: number;
};

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
      const width = Math.max(fallbackLayout.width, Math.ceil(rect.width || fallbackLayout.width));
      const height = Math.max(fallbackLayout.height, Math.ceil(rect.height || fallbackLayout.height));
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
    return 'Runtime';
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

function inputPlaceholder(snapshot: RuntimeSnapshot): string {
  if (!snapshot.liveAvailable) {
    return snapshot.liveUnavailableReason || 'PixelPilot Live unavailable';
  }
  if (snapshot.liveEnabled && snapshot.liveVoiceActive) {
    return 'Type or speak while the mic is active...';
  }
  if (snapshot.liveEnabled) {
    return 'Type or speak to PixelPilot Live...';
  }
  return 'Turn AI on to chat with PixelPilot...';
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
  const errorText = String(localError || runtimeError || '').trim();
  if (errorText) {
    return { kind: 'error', tone: 'error', text: errorText };
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
      label: 'Enable live mode',
      disabled: true,
      description: snapshot.liveUnavailableReason || 'PixelPilot Live unavailable'
    };
  }

  if (!snapshot.liveEnabled) {
    return {
      state: pending ? 'connecting' : 'off',
      label: 'Enable live mode',
      disabled: false,
      description: pending ? 'Turning AI on...' : 'Turn AI on'
    };
  }

  const state = String(snapshot.liveSessionState || '').trim().toLowerCase();
  if (pending || state === 'connecting') {
    return { state: 'connecting', label: 'Disable live mode', disabled: false, description: 'Connecting to AI...' };
  }
  if (state === 'thinking') {
    return { state: 'thinking', label: 'Disable live mode', disabled: false, description: 'AI is thinking' };
  }
  if (state === 'waiting') {
    return { state: 'waiting', label: 'Disable live mode', disabled: false, description: 'Waiting for the current action' };
  }
  if (state === 'acting') {
    return { state: 'acting', label: 'Disable live mode', disabled: false, description: 'AI is working on the task' };
  }
  if (state === 'interrupted') {
    return { state: 'interrupted', label: 'Disable live mode', disabled: false, description: 'AI was interrupted' };
  }
  if (state === 'disconnected') {
    return { state: 'ready', label: 'Disable live mode', disabled: false, description: 'AI is on and ready' };
  }
  return { state: 'connected', label: 'Disable live mode', disabled: false, description: 'AI is on' };
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

  if (!snapshot.liveEnabled) {
    return {
      state: pending ? 'listening_user' : 'idle',
      label: 'Turn AI on and start voice',
      disabled: false,
      description: pending ? 'Turning AI on and starting voice...' : 'Turn AI on and start voice'
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

  if (snapshot.liveEnabled) {
    return { text: 'PixelPilot is running in the background.', busy: false, tone: 'idle' };
  }

  return { text: 'PixelPilot minimized.', busy: false, tone: 'idle' };
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
  onClick
}: {
  snapshot: RuntimeSnapshot;
  pending?: boolean;
  compact?: boolean;
  onClick?: () => void;
}): React.JSX.Element {
  const presentation = liveButtonPresentation(snapshot, pending);
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
      disabled={presentation.disabled}
      onClick={onClick}
      className={[
        'no-drag relative flex items-center justify-center text-slate-800 transition-all',
        compact ? 'h-8 w-10 rounded-xl' : 'h-10 w-10 rounded-2xl',
        presentation.state === 'off' || presentation.state === 'disabled' ? softShell : activeShell,
        presentation.disabled ? 'cursor-not-allowed opacity-45' : 'hover:bg-white/24'
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
  onClick
}: {
  snapshot: RuntimeSnapshot;
  pending?: boolean;
  compact?: boolean;
  onClick?: () => void;
}): React.JSX.Element {
  const presentation = micButtonPresentation(snapshot, pending);
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
      disabled={presentation.disabled}
      onClick={onClick}
      className={[
        'no-drag relative flex items-center justify-center text-slate-800 transition-all',
        compact ? 'h-8 w-10 rounded-xl' : 'h-10 w-10 rounded-2xl',
        presentation.state === 'idle' || presentation.state === 'disabled' ? softShell : activeShell,
        presentation.disabled ? 'cursor-not-allowed opacity-45' : 'hover:bg-white/24'
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

function AudioMeter({
  label,
  level,
  icon
}: {
  label: string;
  level: number;
  icon: React.ReactNode;
}): React.JSX.Element {
  const width = `${Math.max(6, Math.round(clamp(level) * 100))}%`;
  return (
    <div className="min-w-[110px]">
      <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">
        {icon}
        <span>{label}</span>
      </div>
      <div className="h-2.5 rounded-full bg-white/24">
        <motion.div
          className="h-full rounded-full bg-slate-800"
          animate={{ width }}
          transition={{ type: 'spring', stiffness: 180, damping: 22 }}
        />
      </div>
    </div>
  );
}

function LoadingShell({ windowKind }: { windowKind: WindowKind | null }): React.JSX.Element {
  const widthClass =
    windowKind === 'notch'
      ? 'w-[420px]'
      : windowKind === 'sidecar'
        ? 'w-[380px]'
        : windowKind === 'settings'
          ? 'w-[220px]'
          : 'w-[560px]';
  useWindowLayout(
    windowKind === 'notch'
      ? { width: 420, height: 88 }
      : windowKind === 'sidecar'
        ? { width: 380, height: 320 }
        : windowKind === 'settings'
          ? { width: 220, height: 124 }
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
              <div className="text-sm text-slate-600">Connecting the Electron shell to the Python runtime.</div>
            </div>
            <LoaderCircle className="h-5 w-5 animate-spin text-slate-700" />
          </div>
        </GlassPanel>
      </motion.div>
    </div>
  );
}

function AuthGate({
  auth,
  runtimeError,
  onLogin,
  onUseApiKey,
  onQuit
}: {
  auth: AuthState;
  runtimeError: string;
  onLogin: (email: string, password: string) => Promise<void>;
  onUseApiKey: (apiKey: string) => Promise<void>;
  onQuit: () => Promise<void>;
}): React.JSX.Element {
  const [email, setEmail] = useState(auth.email);
  const [password, setPassword] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState('');
  const [statusText, setStatusText] = useState('');
  const [statusIsError, setStatusIsError] = useState(false);
  useWindowLayout({
    width: 420,
    height: 700
  });

  useEffect(() => {
    if (auth.email && !email) {
      setEmail(auth.email);
    }
  }, [auth.email, email]);

  const submitAccount = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!email.trim() || !password) {
      setLocalError('');
      setStatusText('Please enter email and password');
      setStatusIsError(true);
      return;
    }
    setSubmitting(true);
    setLocalError('');
    setStatusText('Signing in...');
    setStatusIsError(false);
    try {
      await onLogin(email, password);
      setPassword('');
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Sign-in failed.');
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
      <div className="drag-region relative overflow-hidden rounded-2xl border border-[rgba(52,78,102,0.72)] bg-[rgba(18,30,44,0.96)] px-8 pb-8 pt-7 shadow-[0_24px_70px_rgba(3,10,18,0.45)]">
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
            Sign in with the tester credentials you were given
          </p>
        </div>

        <form className="mt-8 grid gap-0" onSubmit={(event) => void submitAccount(event)}>
          <label className="mb-2 text-[11px] font-semibold tracking-[0.03em] text-[rgba(207,233,255,0.8)]">
            Email
          </label>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="Enter your email"
            className="no-drag mb-4 min-h-[42px] rounded-[10px] border border-[rgba(52,78,102,0.72)] bg-[rgba(20,36,54,0.78)] px-3.5 py-2.5 text-[13px] text-[#e5f3ff] outline-none transition placeholder:text-[rgba(207,233,255,0.4)] focus:border-[#057FCA]"
          />

          <label className="mb-2 text-[11px] font-semibold tracking-[0.03em] text-[rgba(207,233,255,0.8)]">
            Password
          </label>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Enter your password"
            className="no-drag min-h-[42px] rounded-[10px] border border-[rgba(52,78,102,0.72)] bg-[rgba(20,36,54,0.78)] px-3.5 py-2.5 text-[13px] text-[#e5f3ff] outline-none transition placeholder:text-[rgba(207,233,255,0.4)] focus:border-[#057FCA]"
          />

          <button
            type="submit"
            disabled={submitting}
            className="no-drag mt-6 min-h-[44px] rounded-[10px] bg-[linear-gradient(90deg,#057FCA,#0598e0)] px-4 py-3 text-[13px] font-bold tracking-[0.04em] text-white transition hover:brightness-110 disabled:opacity-45"
          >
            {submitting ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        <p className="mt-6 px-2 text-center text-[11px] leading-5 text-[rgba(207,233,255,0.65)]">
          Registration is disabled. Use the login credentials provided to you.
        </p>

        <div className="mt-8 text-center text-[11px] text-[rgba(207,233,255,0.5)]">
          Or use your own API Key
        </div>

        <form className="mt-3 grid gap-3" onSubmit={(event) => void submitApiKey(event)}>
          <input
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="Paste Gemini API Key (starts with AIza...)"
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
    snapshot.liveAvailable ? humanizeState(snapshot.liveSessionState) : 'Offline'
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

function DetailsPanel({
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
  const visibleMessages = useMemo(() => deferredMessages.slice(-8), [deferredMessages]);
  const visibleUpdates = useMemo(() => updates.slice(-3).reverse(), [updates]);
  const summaryLine = [
    snapshot.operationMode,
    snapshot.visionMode,
    snapshot.workspace === 'agent' ? 'Agent desktop' : 'User desktop',
    snapshot.liveAvailable ? humanizeState(snapshot.liveSessionState) : 'Offline'
  ].join(' · ');

  return (
    <SoftPanel className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Process details</div>
          <div className="mt-1 text-sm text-slate-700">{summaryLine}</div>
        </div>
        <button
          type="button"
          onClick={() => void onStop()}
          disabled={!snapshot.liveEnabled}
          className="no-drag rounded-2xl border border-white/40 bg-white/50 px-4 py-2.5 text-sm font-semibold text-slate-900 transition hover:bg-white/65 disabled:opacity-45"
        >
          Stop current turn
        </button>
      </div>

      {!snapshot.liveAvailable && snapshot.liveUnavailableReason && (
        <div className="mt-3 rounded-2xl border border-amber-200/80 bg-amber-50/80 px-3 py-2.5 text-sm text-amber-900">
          {snapshot.liveUnavailableReason}
        </div>
      )}

      {runtimeError && (
        <div className="mt-3 rounded-2xl border border-rose-200/80 bg-rose-50/80 px-3 py-2.5 text-sm text-rose-900">
          {runtimeError}
        </div>
      )}

      <div className="mt-3 rounded-[22px] border border-white/35 bg-white/20 px-3 py-3">
        {visibleUpdates.length > 0 && (
          <div className="mb-3 space-y-1.5">
            {visibleUpdates.map((update, index) => (
              <div
                key={update.action_id || update.name || `${update.status}-${index}`}
                className="flex items-start gap-2 text-sm text-slate-700"
              >
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${actionTone(update.status)}`} />
                <div className="min-w-0">
                  <div className="truncate font-medium text-slate-900">
                    {update.message || update.name || 'Live action'}
                  </div>
                  {(update.status || update.error) && (
                    <div className="text-xs text-slate-500">{update.error || humanizeState(update.status || '')}</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Conversation</div>
        <div className="space-y-2">
        {visibleMessages.length > 0 ? (
          visibleMessages.map((entry) => (
            <div key={entry.id} className={messageBubbleClass(entry)}>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                {speakerLabel(entry)}
              </div>
              <div className="text-sm">{entry.text}</div>
              {!entry.final && <div className="mt-1 text-[11px] text-slate-500">Streaming...</div>}
            </div>
          ))
        ) : (
          <div className="rounded-2xl border border-dashed border-white/34 bg-white/18 px-3 py-3 text-sm text-slate-600">
            Open apps, send emails/WhatsApp, fix PC issues, or ask anything...
          </div>
        )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-4">
        <AudioMeter label="You" level={snapshot.userAudioLevel} icon={<Mic className="h-3.5 w-3.5" />} />
        <AudioMeter
          label="Assistant"
          level={snapshot.assistantAudioLevel}
          icon={<Waves className="h-3.5 w-3.5" />}
        />
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
  const [optimisticLiveEnabled, setOptimisticLiveEnabled] = useState<boolean | null>(null);
  const [optimisticLiveVoiceActive, setOptimisticLiveVoiceActive] = useState<boolean | null>(null);
  const submittingRef = useRef(false);
  const shellRef = useRef<HTMLDivElement>(null);
  const effectiveSnapshot = useMemo(
    () => ({
      ...snapshot,
      liveEnabled: optimisticLiveEnabled ?? snapshot.liveEnabled,
      liveVoiceActive: optimisticLiveVoiceActive ?? snapshot.liveVoiceActive
    }),
    [optimisticLiveEnabled, optimisticLiveVoiceActive, snapshot]
  );
  const isBarOnly = !effectiveSnapshot.expanded;
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
    effectiveSnapshot.liveEnabled && hasOngoingLiveTurn(effectiveSnapshot, actionUpdates, busyText);
  const inlineStatusToneClass =
    commandBarStatus.tone === 'error'
      ? 'text-rose-600'
      : commandBarStatus.tone === 'placeholder'
        ? 'text-slate-500'
        : 'text-slate-600';

  useEffect(() => {
    if (optimisticLiveEnabled !== null && snapshot.liveEnabled === optimisticLiveEnabled) {
      setOptimisticLiveEnabled(null);
      setPendingLiveToggle(false);
    }
  }, [optimisticLiveEnabled, snapshot.liveEnabled]);

  useEffect(() => {
    if (optimisticLiveVoiceActive !== null && snapshot.liveVoiceActive === optimisticLiveVoiceActive) {
      setOptimisticLiveVoiceActive(null);
      setPendingVoiceToggle(false);
    }
  }, [optimisticLiveVoiceActive, snapshot.liveVoiceActive]);

  const fallbackLayout = {
    width: 920,
    height: (isBarOnly ? 88 : 114) + (effectiveSnapshot.expanded ? 388 : 0)
  };
  useMeasuredWindowLayout(shellRef, fallbackLayout, [
    isBarOnly,
    effectiveSnapshot.expanded,
    effectiveSnapshot.liveEnabled,
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
      setLocalError(error instanceof Error ? error.message : 'Runtime request failed.');
    }
  };

  const submitCommand = async (): Promise<void> => {
    const text = commandText.trim();
    if (!text || submittingRef.current) {
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
    if (!effectiveSnapshot.agentViewEnabled) {
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
    setLocalError('');
    try {
      await window.pixelPilot.toggleSettingsWindow();
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to open settings right now.');
    }
  };

  const toggleLiveMode = async (): Promise<void> => {
    if (pendingLiveToggle) {
      return;
    }
    if (!effectiveSnapshot.liveAvailable) {
      setLocalError(effectiveSnapshot.liveUnavailableReason || 'PixelPilot Live is unavailable right now.');
      return;
    }

    const nextEnabled = !effectiveSnapshot.liveEnabled;
    setLocalError('');
    setBusyText(nextEnabled ? 'Turning AI on...' : 'Turning AI off...');
    setPendingLiveToggle(true);
    setOptimisticLiveEnabled(nextEnabled);
    if (!nextEnabled) {
      setOptimisticLiveVoiceActive(false);
    }

    try {
      const payload = await window.pixelPilot.invokeRuntime('live.setEnabled', { enabled: nextEnabled });
      if (typeof payload.liveEnabled === 'boolean') {
        setOptimisticLiveEnabled(Boolean(payload.liveEnabled));
      }
      if (!nextEnabled) {
        setOptimisticLiveVoiceActive(false);
        setPendingVoiceToggle(false);
      }
      setPendingLiveToggle(false);
      setBusyText('');
    } catch (error) {
      setOptimisticLiveEnabled(null);
      setOptimisticLiveVoiceActive(null);
      setPendingLiveToggle(false);
      setBusyText('');
      setLocalError(error instanceof Error ? error.message : 'Unable to change AI power right now.');
    }
  };

  const toggleVoiceInput = async (): Promise<void> => {
    if (pendingVoiceToggle || pendingLiveToggle) {
      return;
    }
    if (!effectiveSnapshot.liveAvailable) {
      setLocalError(effectiveSnapshot.liveUnavailableReason || 'PixelPilot Live is unavailable right now.');
      return;
    }

    const startingFromOff = !effectiveSnapshot.liveEnabled;
    const nextVoiceState = startingFromOff ? true : !effectiveSnapshot.liveVoiceActive;
    setLocalError('');
    setBusyText(
      nextVoiceState
        ? startingFromOff
          ? 'Turning AI on and starting voice...'
          : 'Starting voice input...'
        : 'Stopping voice input...'
    );
    setPendingVoiceToggle(true);

    try {
      if (startingFromOff) {
        setPendingLiveToggle(true);
        setOptimisticLiveEnabled(true);
        const liveResult = await window.pixelPilot.invokeRuntime('live.setEnabled', { enabled: true });
        const liveEnabled = typeof liveResult.liveEnabled === 'boolean' ? Boolean(liveResult.liveEnabled) : true;
        setOptimisticLiveEnabled(liveEnabled);
        setPendingLiveToggle(false);
        if (!liveEnabled) {
          throw new Error('PixelPilot could not turn AI on.');
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
      setOptimisticLiveEnabled(null);
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
          readOnly={!effectiveSnapshot.liveEnabled}
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
      <MicControlButton snapshot={effectiveSnapshot} pending={pendingVoiceToggle} compact onClick={() => void toggleVoiceInput()} />
      {showInlineStop && (
        <button
          type="button"
          aria-label="Stop current turn"
          title="Stop current turn"
          onClick={() => void invoke('live.stop')}
          className="no-drag flex h-8 items-center justify-center rounded-xl border border-white/36 bg-white/34 px-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-700 transition hover:bg-white/48"
        >
          Stop
        </button>
      )}
      <button
        type="button"
        aria-label="Send command"
        onClick={() => void submitCommand()}
        disabled={!effectiveSnapshot.liveEnabled || !commandText.trim()}
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
                disabled={!effectiveSnapshot.agentViewEnabled}
                onClick={() => void toggleAgentViewFromBar()}
              >
                {workspaceIcon(effectiveSnapshot.workspace)}
              </IconButton>
              <LiveModeButton snapshot={effectiveSnapshot} pending={pendingLiveToggle} compact onClick={() => void toggleLiveMode()} />
              <IconButton compact label="Open settings menu" onClick={() => void openSettingsMenu()}>
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
                disabled={!effectiveSnapshot.agentViewEnabled}
                onClick={() => void toggleAgentViewFromBar()}
              >
                {workspaceIcon(effectiveSnapshot.workspace)}
              </IconButton>
              <LiveModeButton snapshot={effectiveSnapshot} pending={pendingLiveToggle} compact onClick={() => void toggleLiveMode()} />
              <IconButton compact label="Open settings menu" onClick={() => void openSettingsMenu()}>
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

function SettingsShell({ snapshot }: { snapshot: RuntimeSnapshot }): React.JSX.Element {
  const [localError, setLocalError] = useState('');
  const shellRef = useRef<HTMLDivElement>(null);

  useMeasuredWindowLayout(
    shellRef,
    {
      width: 220,
      height: localError ? 284 : 252
    },
    [localError, snapshot.operationMode, snapshot.visionMode]
  );

  const runMenuAction = async (method: string, payload?: Record<string, unknown>): Promise<void> => {
    setLocalError('');
    try {
      await window.pixelPilot.closeSettingsWindow();
      await window.pixelPilot.invokeRuntime(method, payload);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to update settings right now.');
    }
  };

  return (
    <motion.div
      ref={shellRef}
      className="w-full"
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className="w-full rounded-[10px] border border-[#d3dce8] bg-[#f8fafc] p-1.5 shadow-[0_18px_32px_rgba(15,23,42,0.18)]">
        <div className="px-3.5 py-1.5 text-[12px] font-semibold text-slate-500">
          Mode
        </div>
        {modes.map((mode) => (
          <MenuItemButton
            key={mode.id}
            label={mode.label}
            active={snapshot.operationMode === mode.id}
            onClick={() => void runMenuAction('mode.set', { value: mode.id })}
          />
        ))}
        <div className="mx-1 my-1 h-px bg-[#e2e8f0]" />
        <div className="px-3.5 py-1.5 text-[12px] font-semibold text-slate-500">
          Vision
        </div>
        {visions.map((vision) => (
          <MenuItemButton
            key={vision.id}
            label={vision.label}
            active={snapshot.visionMode === vision.id}
            onClick={() => void runMenuAction('vision.set', { value: vision.id })}
          />
        ))}
        <div className="mx-1 my-1 h-px bg-[#e2e8f0]" />
        <button
          type="button"
          onClick={() => void runMenuAction('auth.logout')}
          className="no-drag flex w-full items-center rounded-lg px-3.5 py-2 text-left text-[13px] text-slate-800 transition hover:bg-[#e2e8f0]"
        >
          Sign Out
        </button>
        {localError && (
          <div className="mt-1 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-900">
            {localError}
          </div>
        )}
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
  useWindowLayout({ width: 420, height: 76 });

  const restoreOverlay = async (): Promise<void> => {
    setLocalError('');
    try {
      await window.pixelPilot.setBackgroundHidden(false);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Unable to restore PixelPilot right now.');
    }
  };

  return (
    <motion.div
      className="mx-auto w-[420px]"
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <button
        type="button"
        aria-label="Restore overlay"
        onClick={() => void restoreOverlay()}
        className={`${activeShell} no-drag block w-full rounded-b-[26px] px-4 py-3 text-left transition hover:bg-white/32`}
      >
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl border border-white/40 bg-white/22">
            <PixelPilotLogo className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-slate-900">{progress.text}</div>
          </div>
        </div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/38">
          {progress.busy ? (
            <motion.div
              className="h-full w-[34%] rounded-full bg-slate-900/78"
              animate={{ x: ['-120%', '260%'] }}
              transition={{ duration: 1.7, ease: 'linear', repeat: Number.POSITIVE_INFINITY }}
            />
          ) : (
            <div
              className={[
                'h-full rounded-full',
                progress.tone === 'error'
                  ? 'w-[28%] bg-rose-500/78'
                  : progress.tone === 'active'
                    ? 'w-[38%] bg-sky-500/72'
                    : 'w-[18%] bg-slate-400/70'
              ].join(' ')}
            />
          )}
        </div>
      </button>
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
                    ? 'Waiting for the next frame from the Python desktop capture stream.'
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
          setRuntimeError(error instanceof Error ? error.message : 'Unable to connect to the runtime.');
        });
      }
    };

    void initialize();

    const offState = window.pixelPilot.onState((nextSnapshot) => {
      startTransition(() => {
        setSnapshot(nextSnapshot);
        setMessages(nextSnapshot.recentMessages.map(normalizeEntry));
        setActionUpdates(nextSnapshot.recentActionUpdates);
      });
    });

    const offEvent = window.pixelPilot.onEvent((envelope) => {
      startTransition(() => {
        if (envelope.kind === 'error' || envelope.method === 'runtime.error') {
          setRuntimeError(String(envelope.payload.message || 'Runtime bridge error.'));
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

  if (!ready || !snapshot) {
    return <LoadingShell windowKind={windowKind} />;
  }

  const shellBody = snapshot.auth.needsAuth ? (
    <AuthGate
      auth={snapshot.auth}
      runtimeError={runtimeError}
      onLogin={(email, password) =>
        window.pixelPilot
          .invokeRuntime('auth.login', {
            email,
            password
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
            : windowKind === 'settings'
              ? 'flex items-start justify-start'
            : 'flex items-start justify-center p-3'
      ].join(' ')}
    >
      {windowKind !== 'settings' && (
        <>
          <div className="pointer-events-none absolute left-[8%] top-[-18%] h-28 w-28 rounded-full bg-sky-200/18 blur-3xl" />
          <div className="pointer-events-none absolute bottom-[-20%] right-[10%] h-32 w-32 rounded-full bg-slate-300/18 blur-3xl" />
        </>
      )}
      {shellBody}
    </div>
  );
}
