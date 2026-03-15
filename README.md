# PixelPilot

![PixelPilot Logo](src/logos/pixelpilot-logo-creative.svg)

**Pilot Your Pixels.**

PixelPilot is a Windows desktop AI agent that executes real computer tasks from natural language, using Gemini 3 + computer vision + native system control.

## Hackathon Note (Gemini 3)

We initially planned to integrate Gemini Live API for real-time conversational control. In our current build stack, Live support was available only up to Gemini 2.5, so to satisfy the hackathon Gemini 3 requirement we implemented a **native Gemini 3 request/response pipeline** (Google GenAI SDK) across planning, reasoning, and verification.

- Current implementation: Gemini 3 (request-based) in the production task loop.
- Planned upgrade path: add Gemini Live once Gemini 3 Live support is available for our integration path.

## Problem and Impact

Desktop automation is still fragile for real users: scripts break, app UIs change, and elevation prompts interrupt flows. PixelPilot targets this gap with a robust hybrid agent that can:

- Use vision when the screen matters.
- Use blind/system actions when vision is unnecessary (faster + cheaper).
- Handle Secure Desktop/UAC transitions instead of failing hard.
- Keep risky web and automation tasks in an isolated desktop.

This is useful for operations teams, power users, accessibility workflows, QA/testing flows, and repetitive office tasks.

## Architecture

![High-Level Design View](src/logos/System-Architecture.png)

> [View Detailed Architecture Diagram](src/logos/System-Architecture_Detailed.png)

## Key Technical Features

### Hybrid Planning + Execution
- **Blind-first planning**: every task starts with a blind step to choose `user` vs `agent` workspace before vision.
- **UIA-powered blind mode**: after workspace selection, regular blind turns can inspect UI Automation state and act through the existing keyboard, mouse, skills, and app indexer.
- **Turbo sequences**: Gemini can batch stable sub-actions into one sequence (`action_sequence`) for faster execution.
- **Deferred final replies**: completion responses are buffered until verification passes.

### Vision Stack
- **Lazy vision pipeline**: local OCR + CV first (EasyOCR/OpenCV), Robotics-ER fallback for sparse/ambiguous UI context.
- **Strict modality boundary**: vision uses screenshot/OCR/robotics context only; UI Automation data is reserved for regular blind mode.
- **Incremental screenshots**: screen hash tracking avoids unnecessary recapture/reanalysis when state is unchanged.
- **Reference sheet generation**: cropped element context is assembled for stronger multimodal reasoning.

### Reliability + Safety
- **Secure Desktop/UAC handling**: SYSTEM orchestrator + secure desktop agent for UAC prompt capture and ALLOW/DENY reasoning.
- **Loop detection + recovery**: repeated action/screen patterns trigger alternatives and clarification flow.
- **Mode-aware confirmations**:
  - `GUIDANCE`: interactive tutorial mode.
  - `SAFE`: asks confirmation for dangerous operations.
  - `AUTO`: autonomous execution.

### Workspace Isolation
- **Agent Desktop**: dedicated hidden desktop with custom minimal shell.
- **Dual-workspace UIA observation**: blind mode can inspect both the live `user` desktop and the isolated `agent` desktop.
- **Embedded Agent View**: real-time capture preview shown inside the main GUI (not a floating sidecar window).
- **Process tracking**: spawned processes on agent desktop are tracked and cleaned up on shutdown.

### Desktop GUI
- **Simple bar first**: compact rounded search/action bar as the default UI.
- **Expanded layout**: `Details` panel appears above the search bar.
- **Agent panel placement**: `Agent View` appears below the search bar when enabled/toggled.
- **Workspace-aware Agent View**: Agent View button is visible but disabled on `user` workspace; enabled on `agent` workspace.
- **Background minimize**: minimize hides the app to background/system tray while tasks keep running.

### Skills + OS Integration
- **Skills**: `media`, `browser`, `system`, `timer`.
- **Smart app indexing**: Start Menu + running processes + registry + modern apps for robust `open_app`.
- **UI Automation targeting**: blind mode can target stable `ui_element_id` controls and read UIA-exposed text before escalating to vision.
- **Global hotkeys**: work even when overlay is click-through/unfocused.
- **Voice input**: microphone STT with live level visualization.

## Gemini 3: Where It Is Used

Gemini 3 is used directly in the decision loop:

- **Action planning**: [src/agent/brain.py](src/agent/brain.py)
- **Task completion verification**: [src/agent/verify.py](src/agent/verify.py)
- **Guidance/clarification reasoning**: [src/agent/guidance.py](src/agent/guidance.py), [src/agent/clarification.py](src/agent/clarification.py)

Transport layers:
- **Direct API mode** (`GEMINI_API_KEY` set): [src/backend_client.py](src/backend_client.py) calls Gemini directly.
- **Backend proxy mode** (no local API key): authenticated FastAPI backend relays `/v1/generate`.

## Technical Stack

- **Desktop app**: Python, PySide6
- **Automation/control**: pyautogui, keyboard, ctypes/Win32 APIs
- **Blind UI state**: Windows UI Automation (`uiautomation`)
- **Vision**: EasyOCR, OpenCV, PIL, Gemini Robotics-ER
- **AI SDK**: google-genai
- **Backend (optional)**: FastAPI, MongoDB (auth/users), Redis (rate limit), JWT
- **Web docs/portal (optional)**: React + TypeScript + Vite (`web/`)

## Quick Start

### 1. Install

```bash
python install.py
```

Installer actions:
- Creates `venv` and installs `requirements.txt`
- Prefetches OCR models
- Prebuilds app index cache
- Compiles UAC helpers:
  - [src/uac/orchestrator.py](src/uac/orchestrator.py)
  - [src/uac/agent.py](src/uac/agent.py)
- Creates scheduled tasks:
  - `PixelPilotUACOrchestrator` (SYSTEM, startup)
  - `PixelPilotApp` (launcher)
- Creates desktop shortcut (`Pixel Pilot.lnk`)

Optional (dependencies only):

```bash
python install.py --no-tasks
```

### 2. Configure

Create `.env` in repo root:

```env
# Gemini
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-3-flash-preview

# Mode defaults: guide | safe | auto
DEFAULT_MODE=auto
AGENT_MODE=auto

# Vision mode: ocr | robo
VISION_MODE=ocr

# Blind-mode UIA knobs live in src/config.py
# ENABLE_UIA_BLIND_MODE=True
# UIA_MAX_ELEMENTS=120

# backend override (already set in the codebase)
BACKEND_URL=your_backend_url

# Optional WebSocket gateway token
PIXELPILOT_GATEWAY_TOKEN=pixelpilot-secret
```

You can start from [env.example](env.example).

### 3. Run

Recommended: use the desktop shortcut.

CLI alternative:

```bash
.\venv\Scripts\python.exe .\src\main.py
```

At startup:
- If `GEMINI_API_KEY` exists: direct Gemini mode (no login required).
- If not: login/register dialog uses backend auth, or user can paste API key in the dialog.

Logs:
- `logs/pixelpilot.log`
- `logs/app_launch.log`

## Optional: Run the Backend Locally

Backend files live in `backend/`.

1. Install backend deps:
```bash
cd backend
pip install -r requirements.txt
```

2. Create `backend/.env`:
```env
GEMINI_API_KEY=your_backend_key
MONGODB_URI=your_mongodb_uri
REDIS_URI=redis://localhost:6379
JWT_SECRET=change_me
```

3. Start backend:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

4. Point desktop app to it:
```env
BACKEND_URL=http://localhost:8000
```

Backend API summary:
- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`
- `POST /v1/generate` (JWT-protected, rate limited)
- `GET /health`

## Optional: WebSocket Gateway

Gateway implementation: [src/services/gateway.py](src/services/gateway.py)

Expected message shape:

```json
{
  "auth": "pixelpilot-secret",
  "command": "Open calculator and compute 25*34",
  "params": {
    "mode": "auto"
  }
}
```

## Hotkeys (System-Wide)

- `Ctrl+Shift+Z`: Toggle click-through
- `Ctrl+Shift+X`: Stop current request
- `Ctrl+Shift+Q`: Quit app
- `Ctrl+Shift+M`: Hide/restore app (background minimize toggle)

## Tray Behavior

- Minimize hides PixelPilot to the system tray (background mode).
- Tray menu includes `Show Pixel Pilot`, `Hide to Background`, and `Quit`.
- Tray/task icon uses `pixelpilot-icon` (`src/logos/pixelpilot-icon.ico` when available).


## System Capabilities

- Gemini 3 planning and execution: the agent plans with typed JSON outputs, then executes concrete actions (`click`, `type_text`, `press_key`, `key_combo`, `open_app`, `call_skill`, `sequence`).
- Blind mode routing: step 1 stays workspace-only, while later blind turns can use UI Automation snapshots, `read_ui_text`, and `ui_element_id` targets before requesting vision.
- OCR mode (`VISION_MODE=ocr`): runs local EasyOCR + OpenCV to detect on-screen text and icon candidates with low latency.
- ROBO mode (`VISION_MODE=robo`): uses Gemini Robotics-ER for semantic UI understanding and robust element localization in harder/ambiguous screens.
- Lazy vision routing: starts with blind tools/UIA when they are sufficient, then uses OCR/CV and finally ROBO only when the task cannot be completed safely without vision; screenshot hash checks avoid redundant analysis.
- Workspace-aware automation: starts with blind-first workspace selection, then runs tasks on `user` or isolated `agent` desktop depending on context.
- Reliability and safety: includes UAC secure desktop handling, loop detection/recovery, clarification prompts, blind-first completion verification, and final visual verification when UIA cannot prove completion.
- User control model: supports `GUIDANCE`, `SAFE`, and `AUTO` modes for different autonomy/safety preferences.
- Demo readiness: includes architecture diagrams, explicit Gemini 3 integration points, global hotkeys, gateway payload format, and end-to-end task examples.

## Usage Examples
- "Quick use: Open Calculator and calculate 8125 * 34"
- "Casual use: Find Spotify and play the next song"
- "Maintainance use: Open Device Manager as admin and fix my wifi driver"
- "Search Google for Python tutorials and open the first link"
- "Emergency support mode: open WhatsApp Desktop, send 'I need immediate help, please call me now' to my emergency contact, then share my current location from Google Maps."
- "Critical incident response: open Event Viewer and Windows Security, capture the latest critical errors, and draft a plain-English incident summary for my IT team."
- "Hospital handoff prep: open my active patient handoff note, format it into SBAR structure, and prepare a ready-to-send message for the next shift."
- "Accessibility rescue: I cannot use the mouse right now; open my telehealth portal, navigate to today's appointment page, and stop only when login input is required."
- "Family safety check: open camera feed in browser, save the latest screenshot evidence, and send a timestamped summary to the family group chat."
- "Fraud containment workflow: lock workstation, open bank support page on Agent Desktop, and prepare a step-by-step checklist to freeze cards and reset account access."


## Uninstall

```bash
python uninstall.py
```

Useful flags:
- `--no-tasks`
- `--keep-venv`
- `--keep-dist`
- `--keep-build`
- `--keep-logs`
- `--keep-media`
- `--keep-cache`

## Troubleshooting

- App opens then exits: confirm valid `GEMINI_API_KEY` or working backend login.
- Backend errors: verify `BACKEND_URL` and backend `/health`.
- UAC flow not working:
  - Re-run `python install.py` as Administrator.
  - Confirm `PixelPilotUACOrchestrator` task exists and is running.
- Check logs in `logs/pixelpilot.log`.

---

**Made with Gemini + Advanced Computer Vision.**
