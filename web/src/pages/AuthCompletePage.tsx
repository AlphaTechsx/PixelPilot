import { useEffect, useMemo, useState } from 'react';
import { buildDesktopDeepLink, parseHashPayload } from '../lib/auth';

export function AuthCompletePage() {
  const payload = useMemo(() => parseHashPayload(window.location.hash), []);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!payload) {
      return;
    }
    window.location.assign(buildDesktopDeepLink(payload.code, payload.state));
  }, [payload]);

  const copyCode = async () => {
    if (!payload) {
      return;
    }
    await navigator.clipboard.writeText(payload.code);
    setCopied(true);
  };

  const retryDesktop = () => {
    if (!payload) {
      return;
    }
    window.location.assign(buildDesktopDeepLink(payload.code, payload.state));
  };

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="auth-kicker">PixelPilot Desktop Handoff</div>
        <h1>Return to PixelPilot</h1>
        <p>
          We tried to reopen the desktop app automatically. If nothing happened, retry the deep link
          or copy the one-time code into the desktop sign-in screen.
        </p>

        {!payload ? (
          <div className="auth-error">Missing desktop code. Start the sign-in flow again.</div>
        ) : (
          <>
            <button type="button" className="auth-primary-button" onClick={retryDesktop}>
              Open PixelPilot
            </button>

            <div className="auth-code-block">
              <div className="auth-code-label">One-time browser code</div>
              <code>{payload.code}</code>
            </div>

            <button type="button" className="auth-secondary-button" onClick={copyCode}>
              {copied ? 'Code copied' : 'Copy code'}
            </button>
          </>
        )}
      </section>
    </main>
  );
}
