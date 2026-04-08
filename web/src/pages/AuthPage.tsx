import { useMemo, useState, type FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  buildAuthCompleteHash,
  buildGoogleStartUrl,
  postJson,
  type DesktopCodeResponse,
} from '../lib/auth';

type TokenResponse = {
  access_token: string;
};

export function AuthPage({ mode }: { mode: 'signin' | 'signup' }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const desktopState = useMemo(() => String(searchParams.get('desktop_state') || '').trim(), [searchParams]);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const title = mode === 'signup' ? 'Create your PixelPilot account' : 'Sign in to PixelPilot';
  const subtitle = mode === 'signup'
    ? 'Create an account in the browser, then we will hand you back to the desktop app.'
    : 'Continue with Google or email/password, then return to the desktop automatically.';

  const completeDesktopFlow = async (accessToken: string): Promise<void> => {
    const desktopCode = await postJson<DesktopCodeResponse>(
      '/auth/desktop/issue-code',
      { state: desktopState },
      accessToken
    );
    navigate(`/auth/complete${buildAuthCompleteHash(desktopCode.code, desktopState)}`);
  };

  const submitEmailPassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!desktopState) {
      setError('Missing desktop state. Start again from the PixelPilot desktop app.');
      return;
    }
    if (!email.trim() || !password) {
      setError('Enter your email and password to continue.');
      return;
    }

    setBusy(true);
    setError('');
    try {
      const path = mode === 'signup' ? '/auth/register' : '/auth/login';
      const token = await postJson<TokenResponse>(path, { email, password });
      await completeDesktopFlow(token.access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication failed.');
    } finally {
      setBusy(false);
    }
  };

  const continueWithGoogle = () => {
    if (!desktopState) {
      setError('Missing desktop state. Start again from the PixelPilot desktop app.');
      return;
    }
    window.location.assign(buildGoogleStartUrl(desktopState, mode));
  };

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="auth-kicker">PixelPilot Desktop Handoff</div>
        <h1>{title}</h1>
        <p>{subtitle}</p>

        {!desktopState && (
          <div className="auth-error">
            Missing desktop handoff context. Open this flow from the PixelPilot desktop app.
          </div>
        )}

        <button
          type="button"
          className="auth-primary-button"
          onClick={continueWithGoogle}
          disabled={busy || !desktopState}
        >
          Continue with Google
        </button>

        <div className="auth-divider">or use email and password</div>

        <form className="auth-form" onSubmit={submitEmailPassword}>
          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
            />
          </label>

          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={mode === 'signup' ? 'Create a password' : 'Enter your password'}
            />
          </label>

          <button type="submit" className="auth-secondary-button" disabled={busy || !desktopState}>
            {busy ? 'Working...' : mode === 'signup' ? 'Create account' : 'Sign in'}
          </button>
        </form>

        {error && <div className="auth-error">{error}</div>}
      </section>
    </main>
  );
}
