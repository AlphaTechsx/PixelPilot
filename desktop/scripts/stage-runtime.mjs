import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '..');
const sourceDir = path.join(repoRoot, 'dist');
const targetDir = path.join(process.cwd(), 'resources', 'runtime');
const wakewordSourceDir = path.join(repoRoot, 'models');
const runtimeTargets = ['pixelpilot-runtime', 'orchestrator', 'agent'];
const wakewordTargetDir = path.join(targetDir, 'pixelpilot-runtime', 'wakeword');

fs.rmSync(targetDir, { recursive: true, force: true });
fs.mkdirSync(targetDir, { recursive: true });
fs.mkdirSync(wakewordTargetDir, { recursive: true });

for (const target of runtimeTargets) {
  const source = path.join(sourceDir, target);
  if (!fs.existsSync(source)) {
    throw new Error(`[x] Missing required runtime directory: ${source}`);
  }
  fs.cpSync(source, path.join(targetDir, target), { recursive: true });
}

const wakewordAssets = [
  'pixie.onnx',
  'pixie.onnx.data',
  'melspectrogram.onnx',
  'embedding_model.onnx'
];
for (const file of wakewordAssets) {
  const source = path.join(wakewordSourceDir, file);
  if (!fs.existsSync(source)) {
    continue;
  }
  fs.copyFileSync(source, path.join(wakewordTargetDir, file));
}
