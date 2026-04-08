import { Link } from 'react-router-dom';
import './DocsPage.css';
import { Footer } from '../components/Footer';

const gettingStarted = [
    {
        title: 'Install the desktop app',
        detail:
            'Most users only need the Windows installer. The packaged app registers the deep-link handler, ships the Electron shell, and runs the Python runtime underneath.'
    },
    {
        title: 'Choose an auth path',
        detail:
            'Use browser sign-in or browser sign-up for hosted backend mode, or paste a Gemini API key to run in direct mode without an account gate.'
    },
    {
        title: 'Operate from one shell',
        detail:
            'PixelPilot keeps command entry, Live state, session context, diagnostics, and auth state in the desktop shell instead of splitting that experience across extra tools.'
    }
];

const architectureLayers = [
    {
        title: 'Desktop shell',
        detail:
            'Electron + React render the overlay, auth gate, docs-facing shell states, and desktop deep-link handling for browser-first sign-in.'
    },
    {
        title: 'Runtime bridge',
        detail:
            'A local bridge connects the Electron UI to the Python runtime. Commands such as auth, diagnostics, session restore, and Live controls travel through this bridge.'
    },
    {
        title: 'Python runtime',
        detail:
            'The runtime coordinates Gemini Live, automation policies, session state, hotkeys, UAC flow, tool routing, and activity logging.'
    },
    {
        title: 'Automation and perception',
        detail:
            'Execution starts with UI Automation when possible, then escalates to OCR or visual helpers when semantic structure is missing or unstable.'
    },
    {
        title: 'Optional backend',
        detail:
            'FastAPI provides hosted auth, desktop code redemption, OCR services, Gemini key brokerage, and a JWT-protected Live relay for signed-in users.'
    }
];

const codebaseMap = [
    { name: 'desktop/', detail: 'Electron main process, preload bridge, renderer UI, packaging, and desktop deep-link integration.' },
    { name: 'web/', detail: 'Landing site, hosted auth pages, and public documentation surfaces.' },
    { name: 'src/runtime/', detail: 'Runtime bridge commands, auth status, startup snapshot building, and Electron-facing runtime service glue.' },
    { name: 'src/live/', detail: 'Gemini Live session lifecycle, brokered action execution, tool declarations, and reconnect behavior.' },
    { name: 'src/tools/', detail: 'Desktop action tools, OCR routing, app indexing, and lower-level execution helpers.' },
    { name: 'src/uac/', detail: 'Secure Desktop detection, orchestrator messaging, IPC helpers, and elevation-specific control paths.' },
    { name: 'src/services/', detail: 'Gateway and service-level integrations that sit beside the main runtime.' },
    { name: 'src/auth_manager.py', detail: 'Desktop auth flow orchestration, browser-flow state, secure token persistence, and backend handoff redemption.' },
    { name: 'backend/', detail: 'FastAPI routes, OAuth/JWT auth logic, Redis-backed limits, OCR services, and backend Gemini Live relay.' }
];

const operatorFeatures = [
    {
        title: 'Browser-first authentication',
        bullets: [
            'Desktop app opens hosted sign-in or sign-up in the default browser.',
            'Google OAuth and email/password both end in a one-time desktop code redeem.',
            'Windows secure credential storage persists the backend session token.'
        ]
    },
    {
        title: 'Direct mode',
        bullets: [
            'A local Gemini API key can bypass backend auth entirely.',
            'Direct mode keeps the desktop usable when a hosted backend is not desired.',
            'The auth gate clearly separates direct Gemini usage from hosted account mode.'
        ]
    },
    {
        title: 'Gemini Live runtime',
        bullets: [
            'Typed and voice control share one Live-first runtime model.',
            'Session connection is explicit, and voice capture is no longer forced at startup.',
            'Stop requests and action cancellation respect broker state instead of interrupting blindly.'
        ]
    },
    {
        title: 'Automation model',
        bullets: [
            'GUIDANCE is read-only coaching, SAFE requires approval for mutations, and AUTO runs mutating actions without per-step confirmation.',
            'Hotkeys work globally even when the overlay is unfocused.',
            'Agent Desktop isolation remains available for separated task execution.'
        ]
    },
    {
        title: 'Perception stack',
        bullets: [
            'UI Automation is the preferred path for structure, targeting, and text reads.',
            'EasyOCR-ONNX and OpenCV provide local or backend-hosted OCR and icon cues.',
            'Visual fallbacks only step in when blind automation evidence is insufficient.'
        ]
    },
    {
        title: 'Observability and recovery',
        bullets: [
            'Session context can be resumed from stored summaries.',
            'Diagnostics are exposed through the desktop Settings Hub.',
            'Logs, app index cache, and resumable session artifacts stay on disk for debugging.'
        ]
    }
];

const contributionGuide = [
    {
        title: 'Pick the right surface',
        detail:
            'Desktop UI work usually lives in desktop/, runtime behavior in src/, hosted auth and OCR in backend/, and public marketing/docs changes in web/.'
    },
    {
        title: 'Develop locally with explicit mode choices',
        detail:
            'Use a local GEMINI_API_KEY for direct mode, or point BACKEND_URL and WEB_URL at your hosted or local auth stack for backend mode.'
    },
    {
        title: 'Respect the Windows packaging path',
        detail:
            'Protocol registration, UAC tasks, and packaged runtime binaries all matter. If you change startup, auth handoff, or elevation flow, test the packaged behavior too.'
    },
    {
        title: 'Verify at the layer you changed',
        detail:
            'Run web builds for docs and marketing changes, desktop tests for renderer/main changes, and backend tests for auth or service updates.'
    }
];

const contributorCommands = [
    {
        title: 'Desktop',
        command: `cd desktop\nnpm install\nnpm start`,
        note: 'Use while iterating on Electron renderer or shell behavior.'
    },
    {
        title: 'Web',
        command: `cd web\nnpm install\nnpm run dev`,
        note: 'Use for the landing page, hosted auth pages, and docs route.'
    },
    {
        title: 'Backend',
        command: `cd backend\npip install -r requirements.txt\nuvicorn main:app --host 0.0.0.0 --port 8000 --reload`,
        note: 'Use for hosted auth, OCR routes, Live relay work, and Redis/Mongo-backed flows.'
    }
];

const docsSections = [
    {
        title: 'Environment',
        items: [
            ['Repo root .env', 'Desktop runtime config such as GEMINI_API_KEY, BACKEND_URL, and WEB_URL.'],
            ['backend/.env', 'Hosted service secrets for MongoDB, Redis, JWT, Google OAuth, and backend Gemini services.'],
            ['web/.env.local', 'Frontend build-time values such as VITE_BACKEND_URL for hosted auth pages.']
        ]
    },
    {
        title: 'Testing and verification',
        items: [
            ['Desktop tests', 'Run npm test inside desktop/ for renderer and main-process coverage.'],
            ['Web build and tests', 'Run npm test and npm run build inside web/ before shipping docs or auth UI changes.'],
            ['Python checks', 'Run targeted pytest commands from the repo venv or backend venv depending on the layer touched.']
        ]
    },
    {
        title: 'Operational notes',
        items: [
            ['Logs', 'Check logs/pixelpilot.log for runtime activity and auth-flow issues.'],
            ['Auth storage', 'Desktop tokens live in Windows Credential Manager, with a one-time migration from the legacy auth.json path.'],
            ['Troubleshooting', 'Backend mode depends on reachable MongoDB and Redis plus valid Google OAuth settings for hosted login.']
        ]
    }
];

export const DocsPage = () => {
    return (
        <div className="docs-page">
            <header className="docs-hero">
                <div className="container">
                    <span className="docs-kicker">PIXELPILOT DOCUMENTATION</span>
                    <h1>Product Guide, Architecture Map, and Contributor Handbook</h1>
                    <p>
                        PixelPilot is a Windows desktop automation agent with a browser-first hosted auth
                        flow, a Gemini direct mode, a Live-first runtime, and optional backend services for
                        OCR and authenticated sessions. These docs cover how it works, how the codebase is
                        organized, and how to contribute safely.
                    </p>
                    <div className="docs-hero-actions">
                        <Link className="docs-cta primary" to="/">Back to Landing</Link>
                        <a className="docs-cta" href="https://github.com/AlphaTechsx/PixelPilot" target="_blank" rel="noreferrer">GitHub</a>
                    </div>
                </div>
            </header>

            <section className="docs-section">
                <div className="container">
                    <div className="docs-section-header">
                        <h2>What Ships</h2>
                        <p>The shipped experience is centered on the desktop app, with hosted services only when you want account-backed mode.</p>
                    </div>
                    <div className="docs-grid">
                        {gettingStarted.map((item) => (
                            <article key={item.title} className="docs-card">
                                <h2>{item.title}</h2>
                                <p>{item.detail}</p>
                            </article>
                        ))}
                    </div>
                </div>
            </section>

            <section className="docs-section dark">
                <div className="container">
                    <div className="docs-section-header">
                        <h2>Architecture</h2>
                        <p>PixelPilot spans desktop shell, local runtime, optional backend services, and a hosted browser handoff for sign-in.</p>
                    </div>
                    <div className="docs-diagram">
                        <div className="arch-stack">
                            {architectureLayers.map((item, index) => (
                                <div key={item.title} className="arch-layer">
                                    <article className="arch-node">
                                        <h4>{item.title}</h4>
                                        <p>{item.detail}</p>
                                    </article>
                                    {index < architectureLayers.length - 1 ? (
                                        <div className="arch-lane-connector" aria-hidden="true">|</div>
                                    ) : null}
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </section>

            <section className="docs-section">
                <div className="container">
                    <div className="docs-section-header">
                        <h2>Codebase Structure</h2>
                        <p>This map is near the top on purpose so contributors can orient themselves before changing behavior.</p>
                    </div>
                    <div className="module-grid">
                        {codebaseMap.map((item) => (
                            <div key={item.name} className="module-card">
                                <span>{item.name}</span>
                                <p>{item.detail}</p>
                            </div>
                        ))}
                    </div>
                </div>
            </section>

            <section className="docs-section">
                <div className="container">
                    <div className="docs-section-header">
                        <h2>Detailed Functionality</h2>
                        <p>The product is more than installation. These are the major behavior surfaces users and contributors interact with.</p>
                    </div>
                    <div className="docs-grid">
                        {operatorFeatures.map((item) => (
                            <article key={item.title} className="docs-card">
                                <h2>{item.title}</h2>
                                <ul>
                                    {item.bullets.map((bullet) => (
                                        <li key={bullet}>{bullet}</li>
                                    ))}
                                </ul>
                            </article>
                        ))}
                    </div>
                </div>
            </section>

            <section className="docs-section dark">
                <div className="container">
                    <div className="docs-section-header">
                        <h2>Contributing</h2>
                        <p>Contribution work is easiest when changes stay aligned with the product boundaries above.</p>
                    </div>
                    <div className="docs-grid">
                        {contributionGuide.map((item) => (
                            <article key={item.title} className="docs-card">
                                <h2>{item.title}</h2>
                                <p>{item.detail}</p>
                            </article>
                        ))}
                    </div>
                </div>
            </section>

            <section className="docs-section">
                <div className="container docs-grid">
                    {contributorCommands.map((item) => (
                        <article key={item.title} className="docs-card">
                            <h2>{item.title}</h2>
                            <pre>{item.command}</pre>
                            <p className="docs-note">{item.note}</p>
                        </article>
                    ))}
                </div>
            </section>

            <section className="docs-section">
                <div className="container docs-columns">
                    {docsSections.map((section) => (
                        <div key={section.title}>
                            <h2>{section.title}</h2>
                            <div className="stack">
                                {section.items.map(([label, detail]) => (
                                    <div key={label} className="stack-row">
                                        <span>{label}</span>
                                        <span>{detail}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    ))}
                </div>
            </section>

            <Footer />
        </div>
    );
};
