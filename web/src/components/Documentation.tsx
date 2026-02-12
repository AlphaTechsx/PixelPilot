import './Documentation.css';

const envVars = [
    { key: 'GEMINI_API_KEY', note: 'Required' },
    { key: 'GEMINI_MODEL', note: 'Default: gemini-3-flash-preview' },
    { key: 'DEFAULT_MODE', note: 'guide | safe | auto' },
    { key: 'AGENT_MODE', note: 'Overrides DEFAULT_MODE' },
    { key: 'VISION_MODE', note: 'robo | ocr' },
    { key: 'PIXELPILOT_GATEWAY_TOKEN', note: 'Optional' }
];

export const Documentation = () => {
    return (
        <section id="documentation" className="documentation-section">
            <div className="container">
                <div className="doc-header">
                    <h2 className="doc-title">DOCUMENTATION</h2>
                    <p className="doc-subtitle">Professional setup notes for operators and builders.</p>
                </div>

                <div className="doc-grid">
                    <article className="doc-card">
                        <h3>Installation</h3>
                        <p>Run the installer to set up the environment and desktop launcher.</p>
                        <pre className="doc-code">$ git clone https://github.com/birukabza/Pixel-Pilot.git\n$ cd Pixel-Pilot\n$ python install.py</pre>
                        <div className="doc-note">
                            <span>Optional:</span> <code>python install.py --no-tasks</code>
                        </div>
                        <ul className="doc-list">
                            <li>Creates a virtual environment and installs dependencies.</li>
                            <li>Builds UAC helpers and registers scheduled tasks.</li>
                            <li>Creates a Desktop shortcut to launch the agent.</li>
                        </ul>
                    </article>

                    <article className="doc-card">
                        <h3>Configuration</h3>
                        <p>Create a <code>.env</code> next to <code>install.py</code> (copy from <code>env.example</code>).</p>
                        <div className="doc-env">
                            {envVars.map((item) => (
                                <div key={item.key} className="env-row">
                                    <span className="env-key">{item.key}</span>
                                    <span className="env-note">{item.note}</span>
                                </div>
                            ))}
                        </div>
                    </article>

                    <article className="doc-card">
                        <h3>Run</h3>
                        <p>Use the Desktop shortcut for full permissions. CLI is available for manual runs.</p>
                        <div className="doc-split">
                            <div>
                                <span className="doc-pill">Recommended</span>
                                <p className="doc-strong">Open the PixelPilot Desktop shortcut.</p>
                                <p className="doc-muted">Launches the scheduled task with UAC support.</p>
                            </div>
                            <div>
                                <span className="doc-pill">CLI</span>
                                <pre className="doc-code">$ .\\venv\\Scripts\\python.exe .\\src\\main.py</pre>
                            </div>
                        </div>
                    </article>

                    <article className="doc-card">
                        <h3>Architecture</h3>
                        <ul className="doc-list">
                            <li><strong>Modular Agent Core</strong>: Decoupled <code>core</code>, <code>actions</code>, and <code>capture</code> modules.</li>
                            <li><strong>UAC Orchestrator</strong>: SYSTEM-level service for Secure Desktop interaction.</li>
                            <li><strong>Vision Pipeline</strong>: Dynamic selection between Gemini Robotics-ER and local OCR.</li>
                            <li><strong>Agent Desktop</strong>: Isolated workspace sandbox for safe background automation.</li>
                            <li><strong>Interactive-First</strong>: Mandatory blind planning step for workspace decisions.</li>
                        </ul>
                    </article>

                    <article className="doc-card">
                        <h3>Gateway (Optional)</h3>
                        <p>Run the WebSocket gateway from <code>src/services/gateway.py</code> to drive the agent remotely.</p>
                        <p className="doc-muted">Protect the gateway with <code>PIXELPILOT_GATEWAY_TOKEN</code>.</p>
                    </article>

                    <article className="doc-card">
                        <h3>Troubleshooting & Uninstall</h3>
                        <ul className="doc-list">
                            <li>Verify <code>GEMINI_API_KEY</code> if the app exits immediately.</li>
                            <li>Re-run <code>python install.py</code> as admin for UAC tasks.</li>
                        </ul>
                        <pre className="doc-code">$ python uninstall.py</pre>
                    </article>
                </div>

                <div className="doc-footer">
                    <a href="https://github.com/birukabza/Pixel-Pilot" target="_blank" rel="noreferrer" className="doc-link">
                        View Full Repository Documentation &rarr;
                    </a>
                </div>
            </div>
        </section>
    );
};
