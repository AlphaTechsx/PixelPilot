import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '..');
const sourceDir = path.join(repoRoot, 'dist');
const targetDir = path.join(process.cwd(), 'resources', 'runtime');

fs.mkdirSync(targetDir, { recursive: true });

const candidates = ['pixelpilot-runtime.exe', 'orchestrator.exe', 'agent.exe'];
for (const file of candidates) {
  const source = path.join(sourceDir, file);
  if (!fs.existsSync(source)) {
    continue;
  }
  fs.copyFileSync(source, path.join(targetDir, file));
}
