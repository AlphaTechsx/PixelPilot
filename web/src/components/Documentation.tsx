import './Documentation.css';

const envVars = [
    { key: 'GEMINI_API_KEY', note: 'Optional for direct mode' },
    { key: 'BACKEND_URL', note: 'Use for hosted backend mode' },
    { key: 'WEB_URL', note: 'Browser-first sign-in and sign-up host' }
];

export const Documentation = () => {
    return (
        <section id="documentation" className="documentation-section">
            <div className="container">
                <div className="doc-header">
                    <h2 className="doc-title">DOCUMENTATION</h2>
                    <p className="doc-subtitle">Setup notes for operators, self-hosters, and contributors.</p>
                </div>

                <div className="doc-grid">
                    <article className="doc-card">
                        <h3>Installation</h3>
                        <p>Install the desktop app for the normal user path, or clone the repo for local development.</p>
                        <pre className="doc-code">{`$ git clone https://github.com/AlphaTechsx/PixelPilot.git\n$ cd PixelPilot\n$ python install.py`}</pre>
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
                        <p>Create a <code>.env</code> next to <code>install.py</code> or in the repo root, depending on your workflow.</p>
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
                        <p>Use the Desktop shortcut for full permissions. CLI and hosted web/backend flows are available for development.</p>
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
                            <li><strong>Browser-First Auth</strong>: hosted sign-in/sign-up returns to the desktop through a deep-link handoff.</li>
                            <li><strong>Live-Only Runtime</strong>: Gemini Live powers both typed and voice control paths.</li>
                            <li><strong>UAC Orchestrator</strong>: SYSTEM-level service for Secure Desktop interaction with per-request IPC.</li>
                            <li><strong>Vision Pipeline</strong>: Dynamic selection between Gemini Robotics-ER and local OCR.</li>
                            <li><strong>Agent Desktop</strong>: Isolated workspace sandbox for safe background automation.</li>
                            <li><strong>SAFE Mode</strong>: Mutating Live actions require explicit user confirmation.</li>
                        </ul>
                    </article>

                    <article className="doc-card">
                        <h3>Gateway (Optional)</h3>
                        <p>Enable the gateway to submit remote commands into the same Gemini Live runtime used by the desktop UI.</p>
                        <p className="doc-muted">Protect it with <code>PIXELPILOT_GATEWAY_TOKEN</code>.</p>
                    </article>

                    <article className="doc-card">
                        <h3>Troubleshooting & Uninstall</h3>
                        <ul className="doc-list">
                            <li>Verify <code>GEMINI_API_KEY</code> for direct mode, or confirm <code>BACKEND_URL</code> and hosted auth services for backend mode.</li>
                            <li>Re-run <code>python install.py</code> as admin for UAC tasks.</li>
                        </ul>
                        <pre className="doc-code">$ python uninstall.py</pre>
                    </article>
                </div>

                <div className="doc-footer">
                    <a href="https://github.com/AlphaTechsx/PixelPilot" target="_blank" rel="noreferrer" className="doc-link">
                        View Full Repository Documentation &rarr;
                    </a>
                </div>
            </div>
        </section>
    );
};
