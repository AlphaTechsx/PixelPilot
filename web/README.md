# PixelPilot Web Portal

This is the official web-based interface for **PixelPilot**, a high-performance Windows desktop automation agent.

## Features

- **Live Monitoring**: Real-time view of the Agent Desktop session.
- **Remote Control**: Send natural language commands to your PC from any browser.
- **Documentation**: Built-in guides for setup, configuration, and skill usage.
- **Modern UI**: Powered by React, TypeScript, Vite, and Framer Motion.

## Setup

### 1. Requirements
Ensure you have [Node.js](https://nodejs.org/) installed.

### 2. Installation
```powershell
# Navigate to the web directory
cd web

# Install dependencies
npm install
```

### 3. Development
```powershell
npm run dev
```

### 4. Build
```powershell
npm run build
```

---

**Note**: The web portal communicates with the PixelPilot agent via the WebSocket gateway (configured in `src/services/gateway.py`).
