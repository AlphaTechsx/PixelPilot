# PixelPilot

![PixelPilot Logo](src/logos/pixelpilot-logo-creative.svg)

PixelPilot is a Windows desktop AI agent for real computer work. It combines a desktop shell, a Python runtime, Gemini Live, and optional hosted backend services so users can automate tasks through natural language.

## Tech Stack

[![Electron](https://img.shields.io/badge/Electron-20232A?logo=electron&logoColor=9FEAF9)](https://www.electronjs.org/)
[![React](https://img.shields.io/badge/React-20232A?logo=react&logoColor=61DAFB)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-20232A?logo=typescript&logoColor=3178C6)](https://www.typescriptlang.org/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind-20232A?logo=tailwindcss&logoColor=38BDF8)](https://tailwindcss.com/)
[![Python](https://img.shields.io/badge/Python-20232A?logo=python&logoColor=3776AB)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-20232A?logo=fastapi&logoColor=009688)](https://fastapi.tiangolo.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-20232A?logo=mongodb&logoColor=47A248)](https://www.mongodb.com/)
[![Redis](https://img.shields.io/badge/Redis-20232A?logo=redis&logoColor=DC382D)](https://redis.io/)
[![Google Gemini](https://img.shields.io/badge/Gemini-20232A?logo=google&logoColor=8AB4F8)](https://ai.google.dev/)

## What PixelPilot Does

- Runs as a Windows desktop agent with a compact overlay UI.
- Uses Gemini Live for typed and voice-driven interaction.
- Automates desktop tasks with keyboard, mouse, UI Automation, OCR, and vision fallbacks.
- Supports browser-first account login for hosted backend mode.
- Supports direct mode with a local `GEMINI_API_KEY`.

## Main Modes

- `GUIDANCE`: read-only assistance, no desktop mutations.
- `SAFE`: mutating actions require confirmation.
- `AUTO`: mutating actions run without per-step confirmation.

## Login Model

PixelPilot supports two primary ways to start:

1. `Direct mode`
   Add `GEMINI_API_KEY` locally and launch the app without a login gate.

2. `Hosted backend mode`
   Configure `BACKEND_URL`, launch the app, and sign in from the browser. The browser returns to the desktop app through the `pixelpilot://` deep-link flow.

Desktop backend sessions are stored in Windows Credential Manager.

## Quick Start

### End users

Use the Windows installer and launch PixelPilot.

### Local desktop development

Create a root `.env` from `env.example`:

```env
GEMINI_API_KEY=your_api_key_here
BACKEND_URL=http://localhost:8000
WEB_URL=http://localhost:5173
```

Then run:

```bash
cd desktop
npm install
npm run build:runtime
npm start
```

### Optional backend development

Backend services live in `backend/`.

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Minimal `backend/.env`:

```env
GEMINI_API_KEY=your_backend_key
MONGODB_URI=your_mongodb_uri
REDIS_URI=redis://localhost:6379
JWT_SECRET=change_me
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
WEB_URL=http://localhost:5173
LIVE_SESSION_SECONDS_PER_DAY=600
```

### Optional web development

The public site and hosted auth pages live in `web/`.

```bash
cd web
npm install
npm run dev
```

Use `web/.env.local`:

```env
VITE_BACKEND_URL=http://localhost:8000
```

## Project Structure

- `desktop/`: Electron shell, renderer UI, preload bridge, Windows packaging.
- `src/`: Python runtime, Live orchestration, automation, diagnostics, auth state, UAC flow.
- `backend/`: FastAPI auth, Google OAuth, OCR services, rate limits, Live relay.
- `web/`: landing site, hosted auth pages, public docs.

## Useful Commands

```bash
# Desktop tests
cd desktop
npm test

# Web production build
cd web
npm run build

# Python diagnostics
python src/main.py doctor
```

## Troubleshooting

- No login-free startup: check `GEMINI_API_KEY`.
- Hosted sign-in issues: check `BACKEND_URL`, `WEB_URL`, MongoDB, Redis, and Google OAuth config.
- Runtime issues: check `logs/pixelpilot.log`.
- UAC issues: reinstall from the MSI as Administrator.

## Repository

- GitHub: https://github.com/AlphaTechsx/PixelPilot
- LinkedIn: https://www.linkedin.com/company/pixelpilotai/
- Web: https://pixelpilotai.vercel.app/
