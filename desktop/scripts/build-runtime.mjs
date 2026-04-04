import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '..');
const buildScript = path.join(repoRoot, 'scripts', 'build_runtime.py');
const candidates = [
  process.env.PIXELPILOT_RUNTIME_PYTHON,
  path.join(repoRoot, 'venv', 'Scripts', 'python.exe'),
  'python',
  'py'
].filter(Boolean);

let lastFailure = null;

for (const candidate of candidates) {
  const command = candidate === 'py' ? 'py' : candidate;
  const args = [buildScript];

  if (command !== 'python' && command !== 'py' && !fs.existsSync(command)) {
    continue;
  }

  const result = spawnSync(command, args, {
    cwd: repoRoot,
    stdio: 'inherit',
    env: process.env,
    shell: false
  });

  if (result.error) {
    lastFailure = { command, status: result.status, error: result.error.message };
    continue;
  }

  if (result.status === 0) {
    process.exit(0);
  }

  lastFailure = { command, status: result.status };
}

if (lastFailure) {
  const detail = lastFailure.error ? `: ${lastFailure.error}` : '';
  throw new Error(`Failed to build pixelpilot-runtime.exe with ${lastFailure.command} (status ${lastFailure.status ?? 'unknown'})${detail}`);
}

throw new Error('No usable Python executable was found to build pixelpilot-runtime.exe.');
