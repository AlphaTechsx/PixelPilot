# PixelPilot

![PixelPilot Logo](src/logos/pixelpilot-logo-creative.svg)

PixelPilot is a Windows desktop AI agent that executes computer tasks from natural language using:
- Gemini Live as the only desktop AI runtime for typed and voice input
- Hybrid blind + vision execution
- Native desktop automation (keyboard/mouse/UIA)
- Optional isolated Agent Desktop

## Architecture

![High-Level Design View](src/logos/System-Architecture.png)

## Current Behavior (Important)

### Mode and workspace policy
- Modes are `GUIDANCE`, `SAFE`, `AUTO`.
- All modes are Gemini Live-backed:
  - `GUIDANCE`: read-only tutoring, no desktop actions.
  - `SAFE`: every mutating desktop action requires confirmation.
  - `AUTO`: mutating desktop actions run without per-action confirmation.
- Switching into `GUIDANCE` forces the workspace back to `user`.

### Passthrough/click-through policy
- `agent` workspace: click-through is always OFF.
- `user` workspace: click-through is ON only while a mutating Live action is `queued`, `running`, or `cancel_requested`; otherwise OFF.

### Live startup default
- When Live is available, AI power starts ON by default.
- Turning AI OFF disconnects Gemini Live, stops voice, and disables typed/voice input until AI is turned back on.

### Focus restore on passthrough transitions
- When click-through is disabled, PixelPilot stores the last external foreground window handle.
- When click-through is enabled again, it restores only if that window is minimized, then brings it to foreground (`SetForegroundWindow`).
- Invalid/missing handles are ignored safely.

## Key Features

- UI Automation blind mode with:
  - snapshots
  - `ui_element_id` targeting
  - text extraction (`read_ui_text`)
  - window listing/focus (`list_windows`, `focus_window`)
- Vision pipeline:
  - backend-hosted eye pipeline (`EasyOCR` + OpenCV icon detection) for signed-in users
  - local OCR/CV (`EasyOCR` + OpenCV) for direct API-key mode
  - optional Robotics-ER fallback
  - annotated overlay + optional reference sheet
- UAC secure desktop support through hardened orchestrator/agent helpers (`src/uac/`) with per-request IPC and explicit user confirmation before allow.
- Optional Agent Desktop isolation with sidecar preview and process tracking.
- Skills:
  - `media`
  - `browser`
  - `system`
  - `timer`
- Login/backend mode plus direct API mode.
- Global Windows hotkeys (work even when the overlay is unfocused/click-through).

## Hotkeys

- `Ctrl+Shift+Z`: Toggle click-through manually
- `Ctrl+Shift+X`: Stop the current Live action / session turn
- `Ctrl+Shift+Q`: Quit
- `Ctrl+Shift+M`: Hide/restore app (background toggle)
- `Ctrl+Shift+D`: Toggle details panel

## Desktop Shell Notes

- Desktop shell is Electron-only.
- Python no longer renders any Qt widgets, dialogs, tray UI, or QML surfaces.
- Main states are compact overlay, expanded details, minimized notch, and agent sidecar.
- Minimize hides to background/notch; restore with tray or `Ctrl+Shift+M`.

## Tech Stack

- Desktop shell: Electron + React + TypeScript + Tailwind
- Runtime/backend process: Python + PySide6 Core (headless event loop only)
- AI: Google GenAI SDK (`google-genai`)
- Vision: EasyOCR, OpenCV, Pillow
- Automation: pyautogui, ctypes/Win32, keyboard, UIAutomation (`uiautomation`)
- Live mode audio: PyAudio
- Optional backend: FastAPI + MongoDB + Redis + JWT
- Optional web portal: React + TypeScript + Vite (`web/`)

## Installation

### Standard install

```bash
python install.py
```

Installer does:
- creates `venv`
- installs `requirements.txt`
- prefetches EasyOCR models
- prebuilds app index cache
- compiles packaged desktop runtime (`pixelpilot-runtime.exe`)
- installs/builds the desktop shell in `desktop/`
- compiles UAC helpers (`src/uac/orchestrator.py`, `src/uac/agent.py`)
- creates scheduled tasks:
  - `PixelPilotUACOrchestrator` (SYSTEM startup task)
  - `PixelPilotApp` (launcher task)
- creates desktop shortcut `Pixel Pilot.lnk`

### Dependencies only (skip tasks/shortcut)

```bash
python install.py --no-tasks
```

## Configuration

Create `.env` in repo root (you can start from `env.example`).

```env
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-3-flash-preview

ENABLE_GEMINI_LIVE_MODE=true
LIVE_MODE_DEFAULT_ENABLED=true
LIVE_MODE_DEFAULT_VOICE_ENABLED=true
GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview
LIVE_VOICE_NAME=Leda
LIVE_ENABLE_IMAGE_INPUT=true
LIVE_ENABLE_VIDEO_STREAM=false
LIVE_ENABLE_CONTEXT_WINDOW_COMPRESSION=true
LIVE_VIDEO_FPS=1
LIVE_AUDIO_INPUT_RATE=16000
LIVE_AUDIO_OUTPUT_RATE=24000
LIVE_AUDIO_SPEAKER_QUEUE_MAX_CHUNKS=192
LIVE_AUDIO_LOSSLESS_QUEUE_MAX_CHUNKS=192
LIVE_AUDIO_SPEAKER_QUEUE_TRIM_TO_CHUNKS=144
LIVE_AUDIO_SPEAKER_BATCH_MAX_CHUNKS=8
LIVE_AUDIO_SPEAKER_BATCH_MAX_BYTES=65536
LIVE_AUDIO_LOSSLESS_MODE=true
LIVE_AUDIO_MIC_SUPPRESS_TAIL_MS=220
LIVE_VIDEO_MAX_SECONDS_BEFORE_ROTATE=105

DEFAULT_MODE=auto
VISION_MODE=ocr
BACKEND_URL=your_backend_url

# optional
ENABLE_GATEWAY=false
GATEWAY_HOST=localhost
GATEWAY_PORT=8765
GATEWAY_COMMAND_TIMEOUT_SECONDS=120
PIXELPILOT_GATEWAY_TOKEN=
```

Notes:
- Live mode works in direct mode with a local `GEMINI_API_KEY`, or in backend mode after sign-in when `BACKEND_URL` is configured.
- `LIVE_MODE_DEFAULT_ENABLED=true` means AI power starts enabled whenever Live is available.
- `LIVE_MODE_DEFAULT_VOICE_ENABLED=true` means the mic starts with AI power at startup.
- `LIVE_VOICE_NAME=Leda` selects the prebuilt Live voice. Google documents voice names/styles, not gender labels; `Leda` is listed as a `Youthful` voice and is used here as the closest fit for a girl voice.
- `LIVE_ENABLE_IMAGE_INPUT=true` keeps still-image context available for Gemini 3.1 Flash Live without forcing a continuous video stream.
- `LIVE_ENABLE_VIDEO_STREAM=false` avoids always-on 1 FPS screen streaming by default, which is especially important on Gemini 3.1 Flash Live because video frames are included in turn coverage.
- `LIVE_ENABLE_CONTEXT_WINDOW_COMPRESSION=true` keeps long Live sessions resumable beyond the default audio/video session limits.
- `LIVE_AUDIO_LOSSLESS_MODE=true` keeps assistant audio lossless while using bounded backpressure instead of an unbounded queue.
- `LIVE_AUDIO_LOSSLESS_QUEUE_MAX_CHUNKS` caps how far lossless playback can lag before the receive loop slows down to match the speaker.
- If `GEMINI_API_KEY` is missing, app uses backend auth/proxy mode.

## Run

```bash
cd desktop
npm install
npm run build
npm start
```

Startup behavior:
- Direct mode (`GEMINI_API_KEY` present): Electron opens straight into the shell without a login gate.
- When Live is available, AI power and live voice are enabled by default at startup.
- Backend mode (no local key): Electron shows the auth gate, Gemini Live uses the backend Gemini key after sign-in, and OCR mode runs the full eye pipeline on the backend.
- The desktop shell stays least-privileged at startup; secure desktop/UAC automation uses the installed helper tasks when needed.

Python launcher helper:

```bash
.\venv\Scripts\python.exe .\src\main.py
```

That launcher now starts the Electron shell. It does not open any legacy Python UI.

Windows package build:

```bash
cd desktop
npm run package:win
```

That package flow first builds `pixelpilot-runtime.exe`, then stages it into the Electron app resources before running `electron-builder`.

### Testing Credentials

Use these credentials when testing the public backend:

- Email: `test@example.com`
- Password: `test12345`

## Optional Backend (FastAPI)

Backend code is in `backend/`.

1. Install dependencies:
```bash
cd backend
pip install -r requirements.txt
```

2. Configure `backend/.env`:
```env
GEMINI_API_KEY=your_backend_key
MONGODB_URI=your_mongodb_uri
REDIS_URI=redis://localhost:6379
JWT_SECRET=change_me
OCR_USE_GPU=auto
OCR_REQUESTS_PER_MINUTE=60
OCR_REQUESTS_PER_DAY=1000
GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview
LIVE_VOICE_NAME=Leda
LIVE_MAX_CONCURRENT_SESSIONS=1
LIVE_MAX_ACTIVE_SESSIONS_PER_USER=1
LIVE_SESSION_STARTS_PER_MINUTE=2
LIVE_SESSION_STARTS_PER_DAY=5
LIVE_SESSION_LEASE_TTL_SECONDS=30
LIVE_SESSION_HEARTBEAT_SECONDS=10
```

3. Run:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

4. Point desktop app:
```env
BACKEND_URL=http://localhost:8000
```

Backend endpoints:
- `POST /auth/register` (currently disabled for public testing)
- `POST /auth/login`
- `GET /auth/me`
- `POST /v1/generate` (JWT protected, Redis rate limited)
- `POST /v1/vision/easyocr` (JWT protected, backend EasyOCR helper)
- `POST /v1/vision/local-eye` (JWT protected, backend-hosted OCR + icon detection pipeline)
- `WS /ws/live` (JWT protected, backend Gemini Live relay with Redis-backed Live session limits)
- `GET /health`

Default backend limits:
- Generate: `1000` requests/day per user and `60` requests/minute per user.
- Live: `1` active session globally, `1` active session per user, `2` session starts/minute per user, `5` session starts/day per user, with `30s` leases refreshed every `10s`.


## Optional Gateway

Gateway implementation exists at `src/services/gateway.py`.
Set `ENABLE_GATEWAY=true` to start it with the desktop app.
Gateway commands are executed through the same Gemini Live session used by the desktop UI, so AI power must be on and voice must be idle before a remote command can run.
Set `PIXELPILOT_GATEWAY_TOKEN` explicitly if you want authenticated gateway access.

Expected payload format:

```json
{
  "auth": "set-your-token-here",
  "command": "Open calculator and compute 25*34",
  "params": {
    "mode": "auto"
  }
}
```

## Logs and Runtime Artifacts

- App logs: `logs/pixelpilot.log`
- Launcher logs (scheduled task install path): `logs/app_launch.log`
- Media/debug captures: `media/`
- Auth token cache: `%USERPROFILE%\\.pixelpilot\\auth.json`
- App index cache: `%USERPROFILE%\\.pixelpilot\\app_index.json`

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
