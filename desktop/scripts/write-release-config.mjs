import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '..');
const envPath = path.join(repoRoot, '.env');
const outputPath = path.join(process.cwd(), 'resources', 'release-config.json');

function stripQuotes(value) {
  const trimmed = value.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function parseEnvFile(filePath) {
  if (!fs.existsSync(filePath)) {
    return {};
  }

  const parsed = {};
  const content = fs.readFileSync(filePath, 'utf8');
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      continue;
    }

    const separator = line.indexOf('=');
    if (separator <= 0) {
      continue;
    }

    const key = line.slice(0, separator).trim();
    const value = stripQuotes(line.slice(separator + 1));
    parsed[key] = value;
  }

  return parsed;
}

const envFile = parseEnvFile(envPath);
const backendUrl = String(process.env.BACKEND_URL || envFile.BACKEND_URL || '').trim();
const webUrl = String(process.env.WEB_URL || envFile.WEB_URL || '').trim();

if (!backendUrl) {
  throw new Error('BACKEND_URL is required to create a production-ready MSI release config.');
}

if (!webUrl) {
  throw new Error('WEB_URL is required to create a production-ready MSI release config.');
}

const releaseConfig = {
  backendUrl,
  webUrl,
  generatedAt: new Date().toISOString()
};

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(releaseConfig, null, 2)}\n`, 'utf8');

console.log(`[release-config] wrote ${outputPath}`);
console.log(`[release-config] BACKEND_URL=${backendUrl}`);
console.log(`[release-config] WEB_URL=${webUrl}`);
