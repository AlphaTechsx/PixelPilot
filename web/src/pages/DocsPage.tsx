import { Link } from 'react-router-dom';
import './DocsPage.css';
import { Footer } from '../components/Footer';

const envVars = [
    { key: 'GEMINI_API_KEY', note: 'Required' },
    { key: 'GEMINI_MODEL', note: 'Default: gemini-3-flash-preview' },
    { key: 'DEFAULT_MODE', note: 'guide | safe | auto' },
    { key: 'AGENT_MODE', note: 'Overrides DEFAULT_MODE' },
    { key: 'VISION_MODE', note: 'robo | ocr' },
    { key: 'PIXELPILOT_GATEWAY_TOKEN', note: 'Optional' }
];

const moduleMap = [
    { name: 'src/main.py', detail: 'PySide6 UI, agent loop, and orchestration entry.' },
    { name: 'src/agent', detail: 'Brain, guidance, verification, and clarification layers.' },
    { name: 'src/core', detail: 'Controllers, logging, and app lifecycle glue.' },
    { name: 'src/tools', detail: 'Mouse, keyboard, vision, and app indexing tools.' },
    { name: 'src/skills', detail: 'Browser, media, system, timer skill surfaces.' },
    { name: 'src/desktop', detail: 'Agent Desktop sandbox and preview stream.' },
    { name: 'src/uac', detail: 'Orchestrator and Secure Desktop agent.' },
    { name: 'src/services', detail: 'Audio + gateway service adapters.' },
    { name: 'backend', detail: 'FastAPI service and auth utilities.' }
];

const modeGuide = [
    { mode: 'GUIDANCE', detail: 'Interactive, step-by-step tutorial mode. You do the actions while PixelPilot watches and helps.' },
    { mode: 'SAFE', detail: 'Confirms only potentially dangerous actions (like delete, shutdown).' },
    { mode: 'AUTO', detail: 'Runs fully autonomously without requiring confirmation.' },
    { mode: 'Blind mode', detail: 'When vision is not needed, PixelPilot can plan and act without screenshots and switch back to vision when required.' }
];

const visionGuide = [
    { mode: 'ROBO', detail: 'Gemini Robotics-ER for semantic UI detection.' },
    { mode: 'OCR', detail: 'EasyOCR + OpenCV for fast local parsing.' }
];

export const DocsPage = () => {
    return (
        <div className="docs-page">
            <header className="docs-hero">
                <div className="container">
                    <span className="docs-kicker">PIXELPILOT DOCUMENTATION</span>
                    <h1>Operator Guide, Systems Map, and Runtime Notes</h1>
                    <p>
                        Detailed documentation for the PixelPilot codebase. Powered by the Gemini
                        GenAI SDK with a vision-first automation pipeline and Secure Desktop support.
                    </p>
                    <div className="docs-hero-actions">
                        <Link className="docs-cta primary" to="/">Back to Landing</Link>
                        <a className="docs-cta" href="https://github.com/birukabza/Pixel-Pilot" target="_blank" rel="noreferrer">GitHub</a>
                    </div>
                </div>
            </header>

            <section className="docs-section">
                <div className="container docs-grid">
                    <article className="docs-card">
                        <h2>Install</h2>
                        <p>Run the installer to create the virtual environment and scheduled tasks.</p>
                        <pre>$ git clone https://github.com/birukabza/Pixel-Pilot.git
                            $ cd Pixel-Pilot
                            $ python install.py</pre>
                        <div className="docs-note">Optional: <code>python install.py --no-tasks</code></div>
                        <ul>
                            <li>Builds UAC helpers and scheduled tasks.</li>
                            <li>Creates the Desktop shortcut launcher.</li>
                        </ul>
                    </article>

                    <article className="docs-card">
                        <h2>Configuration</h2>
                        <p>Create a <code>.env</code> in the repo root (copy from <code>env.example</code>). The app will not start without <code>GEMINI_API_KEY</code>.</p>
                        <div className="docs-env">
                            {envVars.map((item) => (
                                <div key={item.key} className="env-row">
                                    <span>{item.key}</span>
                                    <span>{item.note}</span>
                                </div>
                            ))}
                        </div>
                    </article>

                    <article className="docs-card">
                        <h2>Run</h2>
                        <p>Use the Desktop shortcut for full UAC coverage or run manually.</p>
                        <div className="docs-split">
                            <div>
                                <span className="pill">Recommended</span>
                                <p>Open the PixelPilot Desktop shortcut.</p>
                            </div>
                            <div>
                                <span className="pill">CLI</span>
                                <pre>$ .\venv\Scripts\python.exe .\src\main.py</pre>
                            </div>
                        </div>
                    </article>
                </div>
            </section>

            <section className="docs-section dark">
                <div className="container">
                    <div className="docs-section-header">
                        <h2>Architecture</h2>
                        <p>Multi-process layout that bridges userland automation and Secure Desktop.</p>
                    </div>
                    <div className="docs-diagram">
                        <svg viewBox="0 0 900 420" role="img" aria-label="PixelPilot architecture diagram">
                            <defs>
                                <marker id="arrow" markerWidth="10" markerHeight="10" refX="6" refY="3" orient="auto" markerUnits="strokeWidth">
                                    <path d="M0,0 L0,6 L6,3 z" fill="#4EC9B0" />
                                </marker>
                            </defs>
                            <rect x="40" y="40" width="240" height="90" rx="12" className="node" />
                            <text x="60" y="80" className="node-title">Main App</text>
                            <text x="60" y="105" className="node-sub">UI + Agent Loop</text>

                            <rect x="330" y="40" width="240" height="90" rx="12" className="node" />
                            <text x="350" y="80" className="node-title">Vision Pipeline</text>
                            <text x="350" y="105" className="node-sub">Robo + OCR</text>

                            <rect x="620" y="40" width="240" height="90" rx="12" className="node" />
                            <text x="640" y="80" className="node-title">Skills + Tools</text>
                            <text x="640" y="105" className="node-sub">Mouse, Keyboard, OS</text>

                            <rect x="40" y="190" width="240" height="90" rx="12" className="node" />
                            <text x="60" y="230" className="node-title">Agent Desktop</text>
                            <text x="60" y="255" className="node-sub">Isolated Workspace</text>

                            <rect x="330" y="190" width="240" height="90" rx="12" className="node" />
                            <text x="350" y="230" className="node-title">UAC Orchestrator</text>
                            <text x="350" y="255" className="node-sub">SYSTEM Task</text>

                            <rect x="620" y="190" width="240" height="90" rx="12" className="node" />
                            <text x="640" y="230" className="node-title">UAC Agent</text>
                            <text x="640" y="255" className="node-sub">Secure Desktop</text>

                            <rect x="330" y="320" width="240" height="70" rx="12" className="node" />
                            <text x="350" y="360" className="node-title">Gateway (Optional)</text>

                            <line x1="280" y1="85" x2="330" y2="85" className="link" markerEnd="url(#arrow)" />
                            <line x1="570" y1="85" x2="620" y2="85" className="link" markerEnd="url(#arrow)" />
                            <line x1="160" y1="130" x2="160" y2="190" className="link" markerEnd="url(#arrow)" />
                            <line x1="450" y1="130" x2="450" y2="190" className="link" markerEnd="url(#arrow)" />
                            <line x1="740" y1="130" x2="740" y2="190" className="link" markerEnd="url(#arrow)" />
                            <line x1="450" y1="280" x2="450" y2="320" className="link" markerEnd="url(#arrow)" />
                        </svg>
                    </div>
                </div>
            </section>

            <section className="docs-section">
                <div className="container docs-columns">
                    <div>
                        <h2>Operation Modes</h2>
                        <p>Choose the level of autonomy and when PixelPilot should ask for help.</p>
                        <div className="stack">
                            {modeGuide.map((item) => (
                                <div key={item.mode} className="stack-row">
                                    <span>{item.mode}</span>
                                    <span>{item.detail}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                    <div>
                        <h2>Vision</h2>
                        <p>Vision routing selects the fastest reliable path.</p>
                        <div className="stack">
                            {visionGuide.map((item) => (
                                <div key={item.mode} className="stack-row">
                                    <span>{item.mode}</span>
                                    <span>{item.detail}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </section>

            <section className="docs-section">
                <div className="container docs-grid">
                    <article className="docs-card">
                        <h2>Hotkeys</h2>
                        <p>System-wide controls for quick access.</p>
                        <ul>
                            <li><code>Ctrl+Shift+Z</code> Toggle click-through</li>
                            <li><code>Ctrl+Shift+X</code> Stop current request</li>
                            <li><code>Ctrl+Shift+Q</code> Quit PixelPilot</li>
                        </ul>
                    </article>
                    <article className="docs-card">
                        <h2>Gateway (Optional)</h2>
                        <p>Run the WebSocket gateway for remote command execution.</p>
                        <ul>
                            <li>File: <code>src/services/gateway.py</code></li>
                            <li>Protect with <code>PIXELPILOT_GATEWAY_TOKEN</code></li>
                        </ul>
                    </article>
                    <article className="docs-card">
                        <h2>Troubleshooting</h2>
                        <p>Quick checks for common startup issues.</p>
                        <ul>
                            <li>Verify <code>GEMINI_API_KEY</code> in <code>.env</code>.</li>
                            <li>Check <code>logs/pixelpilot.log</code> for errors.</li>
                            <li>Re-run installer as admin if UAC fails.</li>
                        </ul>
                    </article>
                </div>
            </section>

            <section className="docs-section dark">
                <div className="container">
                    <div className="docs-section-header">
                        <h2>Codebase Map</h2>
                        <p>High-signal modules that shape runtime behavior.</p>
                    </div>
                    <div className="module-grid">
                        {moduleMap.map((item) => (
                            <div key={item.name} className="module-card">
                                <span>{item.name}</span>
                                <p>{item.detail}</p>
                            </div>
                        ))}
                    </div>
                </div>
            </section>

            <section className="docs-section">
                <div className="container docs-grid">
                    <article className="docs-card">
                        <h2>Security + UAC</h2>
                        <p>Secure Desktop prompts are handled by the SYSTEM orchestrator task.</p>
                        <ul>
                            <li>Tasks: PixelPilotUACOrchestrator and PixelPilotApp.</li>
                            <li>Orchestrator listens for Secure Desktop triggers.</li>
                            <li>Agent confirms allow/deny with snapshot context.</li>
                        </ul>
                    </article>
                    <article className="docs-card">
                        <h2>Logging</h2>
                        <p>Runtime logs live under <code>logs/</code>.</p>
                        <ul>
                            <li><code>logs/pixelpilot.log</code> for agent activity.</li>
                            <li><code>logs/app_launch.log</code> for launcher tasks.</li>
                        </ul>
                    </article>
                    <article className="docs-card">
                        <h2>Uninstall</h2>
                        <p>Remove tasks, venv, and cached assets.</p>
                        <pre>$ python uninstall.py</pre>
                        <p className="docs-note">Use flags to keep tasks, venv, or logs.</p>
                    </article>
                </div>
            </section>

            <Footer />
        </div>
    );
};
