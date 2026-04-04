import { spawn } from 'node:child_process';
import process from 'node:process';
import electronPath from 'electron';

const args = process.argv.slice(2);
const env = { ...process.env };

delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(electronPath, args, {
  env,
  stdio: 'inherit',
  windowsHide: false
});

child.on('exit', (code, signal) => {
  if (code === null) {
    console.error(`Electron exited with signal ${signal ?? 'unknown'}.`);
    process.exit(1);
    return;
  }
  process.exit(code);
});

child.on('error', (error) => {
  console.error(error);
  process.exit(1);
});
