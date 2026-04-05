import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App.js';
import type {
  PixelPilotApi,
  RendererConfirmationRequest,
  RuntimeEventEnvelope,
  RuntimeSnapshot,
  SidecarFrame,
  WindowKind
} from '@shared/types.js';

type Listener<T> = (value: T) => void;

function makeSnapshot(overrides: Partial<RuntimeSnapshot> = {}): RuntimeSnapshot {
  return {
    operationMode: 'SAFE',
    visionMode: 'OCR',
    workspace: 'user',
    liveAvailable: true,
    liveUnavailableReason: '',
    liveEnabled: true,
    liveVoiceActive: false,
    liveSessionState: 'connected',
    wakeWordEnabled: true,
    wakeWordState: 'armed',
    wakeWordPhrase: 'Hey Pixie',
    wakeWordUnavailableReason: '',
    userAudioLevel: 0.3,
    assistantAudioLevel: 0.1,
    expanded: true,
    backgroundHidden: false,
    agentViewEnabled: true,
    agentViewRequested: false,
    agentViewVisible: false,
    clickThroughEnabled: false,
    agentPreviewAvailable: true,
    sidecarVisible: false,
    auth: {
      signedIn: true,
      directApi: false,
      email: 'dev@example.com',
      userId: 'user-1',
      backendUrl: 'http://localhost:8000',
      hasApiKey: false,
      needsAuth: false
    },
    recentMessages: [
      {
        id: 'user-1',
        kind: 'user',
        text: 'Open the latest project notes.',
        speaker: 'user',
        final: true
      },
      {
        id: 'assistant-1',
        kind: 'assistant',
        text: 'I can search the current workspace and summarize the notes.',
        speaker: 'assistant',
        final: true
      }
    ],
    recentActionUpdates: [
      {
        action_id: 'open',
        name: 'Open notes',
        status: 'running',
        message: 'Launching the current project workspace'
      }
    ],
    ...overrides
  };
}

function setupApi(windowKind: WindowKind, snapshot: RuntimeSnapshot | null): {
  getStartupDefaults: ReturnType<typeof vi.fn>;
  invokeRuntime: ReturnType<typeof vi.fn>;
  setBackgroundHidden: ReturnType<typeof vi.fn>;
  setTrayOnly: ReturnType<typeof vi.fn>;
  setExpanded: ReturnType<typeof vi.fn>;
  toggleSettingsWindow: ReturnType<typeof vi.fn>;
  closeSettingsWindow: ReturnType<typeof vi.fn>;
  toggleStartupSettingsWindow: ReturnType<typeof vi.fn>;
  closeStartupSettingsWindow: ReturnType<typeof vi.fn>;
  setStartupDefaults: ReturnType<typeof vi.fn>;
  updateWindowLayout: ReturnType<typeof vi.fn>;
  resolveConfirmation: ReturnType<typeof vi.fn>;
  quitApp: ReturnType<typeof vi.fn>;
  emitEvent: (event: RuntimeEventEnvelope) => void;
  emitState: (nextSnapshot: RuntimeSnapshot) => void;
  emitConfirmation: (request: RendererConfirmationRequest) => void;
  emitSidecar: (frame: SidecarFrame) => void;
} {
  let stateListener: Listener<RuntimeSnapshot> | null = null;
  let eventListener: Listener<RuntimeEventEnvelope> | null = null;
  let confirmationListener: Listener<RendererConfirmationRequest> | null = null;
  let sidecarListener: Listener<SidecarFrame> | null = null;

  const invokeRuntime = vi.fn().mockResolvedValue({});
  const getStartupDefaults = vi.fn().mockResolvedValue({
    operationMode: (snapshot?.operationMode || 'AUTO').toUpperCase(),
    visionMode: (snapshot?.visionMode || 'OCR').toUpperCase(),
    hasPersisted: false,
    source: 'runtime'
  });
  const setBackgroundHidden = vi.fn().mockResolvedValue({});
  const setTrayOnly = vi.fn().mockResolvedValue({});
  const setExpanded = vi.fn().mockResolvedValue({});
  const toggleSettingsWindow = vi.fn().mockResolvedValue({ visible: true });
  const closeSettingsWindow = vi.fn().mockResolvedValue({ visible: false });
  const toggleStartupSettingsWindow = vi.fn().mockResolvedValue({ visible: true });
  const closeStartupSettingsWindow = vi.fn().mockResolvedValue({ visible: false });
  const setStartupDefaults = vi.fn().mockResolvedValue({
    operationMode: (snapshot?.operationMode || 'AUTO').toUpperCase(),
    visionMode: (snapshot?.visionMode || 'OCR').toUpperCase(),
    hasPersisted: true,
    source: 'persisted'
  });
  const updateWindowLayout = vi.fn().mockResolvedValue(undefined);
  const resolveConfirmation = vi.fn().mockResolvedValue({});
  const quitApp = vi.fn().mockResolvedValue(undefined);

  const api: PixelPilotApi = {
    getWindowKind: vi.fn().mockResolvedValue(windowKind),
    getSnapshot: vi.fn().mockResolvedValue(snapshot),
    getStartupDefaults,
    invokeRuntime,
    setExpanded,
    setBackgroundHidden,
    setTrayOnly,
    toggleSettingsWindow,
    closeSettingsWindow,
    toggleStartupSettingsWindow,
    closeStartupSettingsWindow,
    setStartupDefaults,
    updateWindowLayout,
    resolveConfirmation,
    quitApp,
    onState: (listener) => {
      stateListener = listener;
      return () => {
        stateListener = null;
      };
    },
    onEvent: (listener) => {
      eventListener = listener;
      return () => {
        eventListener = null;
      };
    },
    onConfirmationRequest: (listener) => {
      confirmationListener = listener;
      return () => {
        confirmationListener = null;
      };
    },
    onSidecarFrame: (listener) => {
      sidecarListener = listener;
      return () => {
        sidecarListener = null;
      };
    }
  };

  Object.defineProperty(window, 'pixelPilot', {
    configurable: true,
    value: api
  });

  return {
    getStartupDefaults,
    invokeRuntime,
    setBackgroundHidden,
    setTrayOnly,
    setExpanded,
    toggleSettingsWindow,
    closeSettingsWindow,
    toggleStartupSettingsWindow,
    closeStartupSettingsWindow,
    setStartupDefaults,
    updateWindowLayout,
    resolveConfirmation,
    quitApp,
    emitEvent: (event) => {
      act(() => {
        eventListener?.(event);
      });
    },
    emitState: (nextSnapshot) => {
      act(() => {
        stateListener?.(nextSnapshot);
      });
    },
    emitConfirmation: (request) => {
      act(() => {
        confirmationListener?.(request);
      });
    },
    emitSidecar: (frame) => {
      act(() => {
        sidecarListener?.(frame);
      });
    }
  };
}

describe('Electron renderer App', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the auth gate and submits a direct API key', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        auth: {
          signedIn: false,
          directApi: false,
          email: '',
          userId: '',
          backendUrl: 'http://localhost:8000',
          hasApiKey: false,
          needsAuth: true
        }
      })
    );

    render(<App />);

    expect(await screen.findByRole('heading', { name: /welcome back/i })).toBeInTheDocument();

    await userEvent.type(
      await screen.findByPlaceholderText(/paste gemini api key \(starts with aiza/i),
      'AIza-demo-key'
    );
    await userEvent.click(screen.getByRole('button', { name: /use api key/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('auth.useApiKey', { apiKey: 'AIza-demo-key' });
    });
  });

  it('submits account login from the auth gate and can quit the app', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        auth: {
          signedIn: false,
          directApi: false,
          email: '',
          userId: '',
          backendUrl: 'http://localhost:8000',
          hasApiKey: false,
          needsAuth: true
        }
      })
    );

    render(<App />);

    await userEvent.type(await screen.findByPlaceholderText(/enter your email/i), 'dev@example.com');
    await userEvent.type(screen.getByPlaceholderText(/enter your password/i), 'secret');
    await userEvent.click(screen.getByRole('button', { name: /^sign in$/i }));
    await userEvent.click(screen.getByRole('button', { name: /close login dialog/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('auth.login', {
        email: 'dev@example.com',
        password: 'secret'
      });
      expect(controls.quitApp).toHaveBeenCalled();
    });
  });

  it('renders the overlay shell and keeps the workspace badge disabled in user desktop', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        workspace: 'user',
        agentViewEnabled: false,
        agentViewRequested: false
      })
    );

    render(<App />);

    expect(await screen.findByPlaceholderText(/type a command/i)).toBeInTheDocument();

    const workspaceBadge = screen.getByRole('button', { name: /current workspace: user desktop/i });
    expect(workspaceBadge).toBeDisabled();
    await userEvent.click(workspaceBadge);

    await waitFor(() => {
      expect(controls.invokeRuntime).not.toHaveBeenCalledWith('workspace.set', expect.anything());
      expect(controls.invokeRuntime).not.toHaveBeenCalledWith('agentView.setRequested', expect.anything());
    });
  });

  it('uses the workspace badge to hide the agent preview when already in agent desktop', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        workspace: 'agent',
        agentViewEnabled: true,
        agentViewRequested: true
      })
    );

    render(<App />);

    const workspaceBadge = await screen.findByRole('button', {
      name: /current workspace: agent desktop\. click to hide the agent view/i
    });
    expect(workspaceBadge).toBeEnabled();

    await userEvent.click(workspaceBadge);

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('agentView.setRequested', { requested: false });
    });
  });

  it('wires the overlay toolbar controls to the expected Electron and runtime actions', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        expanded: false,
        workspace: 'agent',
        agentViewEnabled: true,
        agentViewRequested: true
      })
    );

    render(<App />);

    const commandInput = await screen.findByPlaceholderText(/type a command/i);
    await userEvent.type(commandInput, 'Summarize the open window');
    await userEvent.click(screen.getByRole('button', { name: /send command/i }));
    await userEvent.click(screen.getByRole('button', { name: /current workspace: agent desktop\. click to hide the agent view/i }));
    await userEvent.click(screen.getByRole('button', { name: /enable voice input/i }));
    await userEvent.click(screen.getByRole('button', { name: /disconnect live session/i }));
    await userEvent.click(screen.getByRole('button', { name: /open settings menu/i }));
    await userEvent.click(screen.getByRole('button', { name: /hide to notch/i }));
    await userEvent.click(screen.getByRole('button', { name: /expand details/i }));
    await userEvent.click(screen.getByRole('button', { name: /quit pixelpilot/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('live.submitText', { text: 'Summarize the open window' });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('agentView.setRequested', { requested: false });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('live.setEnabled', { enabled: false });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('live.setVoice', { enabled: true });
      expect(controls.toggleSettingsWindow).toHaveBeenCalled();
      expect(controls.setBackgroundHidden).toHaveBeenCalledWith(true);
      expect(controls.setExpanded).toHaveBeenCalledWith(true);
      expect(controls.quitApp).toHaveBeenCalled();
      expect(controls.updateWindowLayout).toHaveBeenCalled();
    });
  });

  it('can reconnect and start voice from the mic control when disconnected', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        liveEnabled: true,
        liveVoiceActive: false,
        liveSessionState: 'disconnected'
      })
    );

    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: /reconnect and start voice/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('live.setEnabled', { enabled: true });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('live.setVoice', { enabled: true });
    });
  });

  it('renders progress and final replies inside the command bar text lane', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        expanded: false,
        liveSessionState: 'acting',
        recentMessages: [
          {
            id: 'user-1',
            kind: 'user',
            text: 'Read the current screen.',
            speaker: 'user',
            final: true
          },
          {
            id: 'assistant-1',
            kind: 'assistant',
            text: 'I found the latest notes.',
            speaker: 'assistant',
            final: true
          }
        ],
        recentActionUpdates: [
          {
            action_id: 'ocr',
            name: 'OCR',
            status: 'running',
            message: 'OCR running on the current screen'
          }
        ]
      })
    );

    render(<App />);

    expect(await screen.findByText(/ocr running on the current screen/i)).toBeInTheDocument();
    expect(screen.queryByText(/i found the latest notes\./i)).not.toBeInTheDocument();

    controls.emitState(
      makeSnapshot({
        expanded: false,
        liveSessionState: 'connected',
        recentMessages: [
          {
            id: 'user-1',
            kind: 'user',
            text: 'Read the current screen.',
            speaker: 'user',
            final: true
          },
          {
            id: 'assistant-1',
            kind: 'assistant',
            text: 'I found the latest notes.',
            speaker: 'assistant',
            final: true
          }
        ],
        recentActionUpdates: [
          {
            action_id: 'ocr',
            name: 'OCR',
            status: 'done',
            message: 'OCR complete',
            done: true
          }
        ]
      })
    );

    expect(await screen.findByText(/i found the latest notes\./i)).toBeInTheDocument();
  });

  it('shows a compact stop control in the command bar while a task is running', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        expanded: false,
        liveSessionState: 'acting',
        recentActionUpdates: [
          {
            action_id: 'open',
            name: 'Open notes',
            status: 'running',
            message: 'Launching the current project workspace'
          }
        ]
      })
    );

    render(<App />);

    const stopButton = await screen.findByRole('button', { name: /stop current turn/i });
    expect(stopButton).toHaveTextContent(/^stop$/i);

    await userEvent.click(stopButton);

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('live.stop', undefined);
    });
  });

  it('opens the separate settings window instead of rendering settings inside the overlay', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        expanded: true
      })
    );

    render(<App />);

    expect(await screen.findByText(/process details/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /open settings menu/i }));

    await waitFor(() => {
      expect(controls.toggleSettingsWindow).toHaveBeenCalled();
      expect(screen.queryByRole('button', { name: /auto/i })).not.toBeInTheDocument();
    });
  });

  it('renders the separate settings window and wires the menu actions', async () => {
    const controls = setupApi(
      'settings',
      makeSnapshot({
        operationMode: 'SAFE',
        visionMode: 'OCR'
      })
    );

    render(<App />);

    expect(await screen.findByText(/^mode$/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /wake word on/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /wake word off/i })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^auto$/i }));
    await userEvent.click(screen.getByRole('button', { name: /^robo$/i }));
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }));

    await waitFor(() => {
      expect(controls.closeSettingsWindow).toHaveBeenCalledTimes(3);
      expect(controls.invokeRuntime).toHaveBeenCalledWith('mode.set', { value: 'AUTO' });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('vision.set', { value: 'ROBO' });
      expect(controls.invokeRuntime).toHaveBeenCalledWith('auth.logout', undefined);
    });
  });

  it('opens startup defaults popup from settings list', async () => {
    const controls = setupApi('settings', makeSnapshot({ operationMode: 'SAFE', visionMode: 'OCR' }));

    render(<App />);

    expect(await screen.findByText(/^mode$/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /startup defaults/i }));

    await waitFor(() => {
      expect(controls.closeSettingsWindow).toHaveBeenCalledTimes(1);
      expect(controls.toggleStartupSettingsWindow).toHaveBeenCalledTimes(1);
    });
  });

  it('renders startup defaults popup and saves persisted defaults', async () => {
    const controls = setupApi(
      'startup-settings',
      makeSnapshot({ operationMode: 'SAFE', visionMode: 'OCR' })
    );
    controls.getStartupDefaults.mockResolvedValueOnce({
      operationMode: 'SAFE',
      visionMode: 'OCR',
      hasPersisted: false,
      source: 'runtime'
    });

    render(<App />);

    expect(await screen.findByText(/^Startup Defaults$/i)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /^auto$/i })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^auto$/i }));
    await userEvent.click(screen.getByRole('button', { name: /^robo$/i }));
    await userEvent.click(screen.getByRole('button', { name: /save startup defaults/i }));

    await waitFor(() => {
      expect(controls.setStartupDefaults).toHaveBeenCalledWith({
        operationMode: 'AUTO',
        visionMode: 'ROBO'
      });
    });
  });

  it('shows only user and ai messages in the expanded log and renders expandable task thinking state', async () => {
    setupApi(
      'overlay',
      makeSnapshot({
        expanded: true,
        liveSessionState: 'acting',
        recentMessages: [
          {
            id: 'activity-1',
            kind: 'activity',
            text: 'Launching the current project workspace',
            speaker: '',
            final: true
          },
          {
            id: 'user-1',
            kind: 'user',
            text: 'Open the latest project notes.',
            speaker: 'user',
            final: true
          },
          {
            id: 'assistant-1',
            kind: 'assistant',
            text: 'I can search the current workspace and summarize the notes.',
            speaker: 'assistant',
            final: true
          },
          {
            id: 'system-1',
            kind: 'system',
            text: 'Bridge connected.',
            speaker: 'system',
            final: true
          }
        ],
        recentActionUpdates: [
          {
            action_id: 'plan',
            name: 'Plan next action',
            status: 'queued',
            message: 'Planning the next action'
          },
          {
            action_id: 'open',
            name: 'Open notes',
            status: 'running',
            message: 'Launching the current project workspace'
          }
        ]
      })
    );

    render(<App />);

    expect(await screen.findByText(/process details/i)).toBeInTheDocument();
    expect(screen.getByText(/wake word enabled/i)).toBeInTheDocument();
    expect(screen.getByText(/open the latest project notes\./i)).toBeInTheDocument();
    expect(screen.getByText(/i can search the current workspace and summarize the notes\./i)).toBeInTheDocument();
    expect(screen.queryByText(/bridge connected\./i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /thinking>/i })).toBeInTheDocument();
    expect(screen.queryByText(/planning the next action/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /thinking>/i }));

    expect(screen.getByText(/planning the next action/i)).toBeInTheDocument();
  });

  it('auto-scrolls the expanded log when new conversation content arrives', async () => {
    const controls = setupApi(
      'overlay',
      makeSnapshot({
        expanded: true,
        recentActionUpdates: []
      })
    );

    const view = render(<App />);

    expect(await screen.findByText(/process details/i)).toBeInTheDocument();

    const scrollArea = view.container.querySelector('.overflow-y-auto') as HTMLDivElement | null;
    expect(scrollArea).not.toBeNull();
    if (!scrollArea) {
      return;
    }

    Object.defineProperty(scrollArea, 'scrollHeight', {
      configurable: true,
      value: 480
    });
    scrollArea.scrollTop = 0;

    controls.emitState(
      makeSnapshot({
        expanded: true,
        recentActionUpdates: [],
        recentMessages: [
          {
            id: 'user-1',
            kind: 'user',
            text: 'First message',
            speaker: 'user',
            final: true
          },
          {
            id: 'assistant-1',
            kind: 'assistant',
            text: 'First response',
            speaker: 'assistant',
            final: true
          },
          {
            id: 'user-2',
            kind: 'user',
            text: 'Second message',
            speaker: 'user',
            final: true
          },
          {
            id: 'assistant-2',
            kind: 'assistant',
            text: 'Second response',
            speaker: 'assistant',
            final: true
          }
        ]
      })
    );

    await waitFor(() => {
      expect(scrollArea.scrollTop).toBe(480);
    });
  });

  it('does not show task thinking state for a simple assistant reply', async () => {
    setupApi(
      'overlay',
      makeSnapshot({
        expanded: true,
        liveSessionState: 'thinking',
        recentActionUpdates: []
      })
    );

    render(<App />);

    expect(await screen.findByText(/process details/i)).toBeInTheDocument();
    expect(screen.queryByText(/thinking>/i)).not.toBeInTheDocument();
  });

  it('renders the notch shell and restores the overlay window', async () => {
    const controls = setupApi(
      'notch',
      makeSnapshot({
        backgroundHidden: true,
        liveEnabled: true,
        liveSessionState: 'disconnected',
        wakeWordEnabled: false,
        wakeWordState: 'disabled',
        recentActionUpdates: []
      })
    );

    render(<App />);

    expect(await screen.findByText(/gemini live is disconnected/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /restore overlay/i }));

    await waitFor(() => {
      expect(controls.setBackgroundHidden).toHaveBeenCalledWith(false);
    });
  });

  it('enters tray-only mode from the notch controls', async () => {
    const controls = setupApi(
      'notch',
      makeSnapshot({
        backgroundHidden: true,
        recentActionUpdates: []
      })
    );

    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: /run in tray only/i }));
    await waitFor(() => {
      expect(controls.setTrayOnly).toHaveBeenCalledWith(true);
    });
  });

  it('shows a notch error when restoring the overlay fails', async () => {
    const controls = setupApi(
      'notch',
      makeSnapshot({
        backgroundHidden: true,
        recentActionUpdates: []
      })
    );
    controls.setBackgroundHidden.mockRejectedValueOnce(new Error('Bridge still connecting'));

    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: /restore overlay/i }));

    const matches = await screen.findAllByText(/bridge still connecting/i);
    expect(matches.length).toBeGreaterThan(0);
  });

  it('renders the sidecar shell and shows incoming preview frames', async () => {
    const controls = setupApi(
      'sidecar',
      makeSnapshot({
        workspace: 'agent',
        sidecarVisible: true,
        agentViewRequested: true
      })
    );

    render(<App />);

    expect(await screen.findByText(/isolated sidecar preview/i)).toBeInTheDocument();

    controls.emitSidecar({
      width: 4,
      height: 4,
      timestamp: Date.now(),
      dataUrl: 'data:image/jpeg;base64,AAAA'
    });

    expect(await screen.findByAltText(/agent desktop preview/i)).toBeInTheDocument();
  });

  it('wires the sidecar buttons for visibility and returning to the overlay', async () => {
    const controls = setupApi(
      'sidecar',
      makeSnapshot({
        workspace: 'agent',
        sidecarVisible: true,
        agentViewRequested: true
      })
    );

    render(<App />);

    expect(await screen.findByText(/isolated sidecar preview/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /hide sidecar/i }));
    await userEvent.click(screen.getByRole('button', { name: /return to bar/i }));

    await waitFor(() => {
      expect(controls.invokeRuntime).toHaveBeenCalledWith('agentView.setRequested', { requested: false });
      expect(controls.setBackgroundHidden).toHaveBeenCalledWith(false);
    });
  });

  it('shows a sidecar error when returning to the bar fails', async () => {
    const controls = setupApi(
      'sidecar',
      makeSnapshot({
        workspace: 'agent',
        sidecarVisible: true,
        agentViewRequested: true
      })
    );
    controls.setBackgroundHidden.mockRejectedValueOnce(new Error('Runtime reconnecting'));

    render(<App />);

    await userEvent.click(await screen.findByRole('button', { name: /return to bar/i }));

    expect(await screen.findByText(/runtime reconnecting/i)).toBeInTheDocument();
  });

  it('opens a confirmation modal and resolves approval', async () => {
    const controls = setupApi('overlay', makeSnapshot());

    render(<App />);

    expect(await screen.findByText(/process details/i)).toBeInTheDocument();

    controls.emitConfirmation({
      id: 'confirm-1',
      title: 'Approve action',
      text: 'Allow PixelPilot to click the Send button?'
    });

    expect(await screen.findByText(/approve action/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /approve/i }));

    await waitFor(() => {
      expect(controls.resolveConfirmation).toHaveBeenCalledWith('confirm-1', { approved: true });
    });
  });
});
