#!/usr/bin/env node
'use strict';

/**
 * grok-jwt-ensure.js
 * Checkt de Grok-CLI JWT in ~/.grok/auth.json. Boven de drempel: klaar.
 * Anders: korte headless `grok models`-run zodat de CLI het token kan
 * refreshen. Alleen de CLI kan dat; de proxy leest alleen.
 *
 * GROK_JWT_MIN_MINUTES  minimale resterende geldigheid (default 30)
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const AUTH = path.join(os.homedir(), '.grok', 'auth.json');
const MIN_MINUTES = Number(process.env.GROK_JWT_MIN_MINUTES);
const DREMPEL_MIN = Number.isFinite(MIN_MINUTES) && MIN_MINUTES >= 0 ? MIN_MINUTES : 30;
const TIMEOUT_MS = 60_000;
const JWT_RE = /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/;

function fail(msg) {
  console.error(msg);
  process.exit(1);
}

function leesExp() {
  let raw;
  try {
    raw = fs.readFileSync(AUTH, 'utf8');
  } catch {
    return null;
  }
  const m = raw.match(JWT_RE);
  if (!m) return null;
  const parts = m[0].split('.');
  const padded = parts[1] + '='.repeat((4 - (parts[1].length % 4)) % 4);
  let payload;
  try {
    payload = JSON.parse(Buffer.from(padded, 'base64url').toString('utf8'));
  } catch {
    return null;
  }
  return typeof payload.exp === 'number' ? payload.exp : null;
}

function resterendMin(exp) {
  return (exp - Date.now() / 1000) / 60;
}

function killTree(child) {
  if (!child.pid) return;
  if (process.platform === 'win32') {
    spawn('taskkill.exe', ['/pid', String(child.pid), '/t', '/f'], {
      stdio: 'ignore',
      windowsHide: true,
    });
    return;
  }
  child.kill('SIGTERM');
}

function resolveGrok() {
  const dir = path.join(os.homedir(), '.grok', 'bin');
  for (const naam of ['grok.exe', 'grok']) {
    const p = path.join(dir, naam);
    if (fs.existsSync(p)) return p;
  }
  return 'grok';
}

function grokModels() {
  return new Promise((resolve, reject) => {
    const bin = resolveGrok();
    // Volledig pad: geen shell. Alleen 'grok' op Windows: cmd voor PATH.
    const viaShell = process.platform === 'win32' && !path.isAbsolute(bin);
    const child = viaShell
      ? spawn('grok models', {
          shell: true,
          windowsHide: true,
          stdio: ['ignore', 'pipe', 'pipe'],
          env: process.env,
        })
      : spawn(bin, ['models'], {
          windowsHide: true,
          stdio: ['ignore', 'pipe', 'pipe'],
          env: process.env,
        });
    let stderr = '';
    child.stderr.on('data', (c) => { stderr += c.toString(); });
    child.stdout.resume();

    const timer = setTimeout(() => {
      killTree(child);
      reject(new Error('timeout 60s'));
    }, TIMEOUT_MS);

    child.on('error', (err) => {
      clearTimeout(timer);
      reject(err);
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) {
        resolve();
        return;
      }
      const hint = stderr.trim().split('\n').pop() || `exit ${code}`;
      reject(new Error(hint));
    });
  });
}

async function main() {
  const voor = leesExp();
  if (voor == null) {
    fail('geen JWT in ~/.grok/auth.json. open de Grok CLI handmatig');
  }

  if (resterendMin(voor) > DREMPEL_MIN) {
    console.log(`OK ${voor}`);
    process.exit(0);
  }

  console.error(`JWT rest ${resterendMin(voor).toFixed(1)} min (drempel ${DREMPEL_MIN}), refresh via grok models`);
  try {
    await grokModels();
  } catch (err) {
    fail(`refresh mislukt (${err.message}). open de Grok CLI handmatig`);
  }

  const na = leesExp();
  if (na == null) {
    fail('refresh niet gelukt: JWT weg na grok-run. open de Grok CLI handmatig');
  }
  if (na > voor) {
    console.log(`OK ${na} (was ${voor})`);
    process.exit(0);
  }
  if (resterendMin(na) > 0) {
    console.log(`OK ${na} (niet opgeschoven)`);
    process.exit(0);
  }
  fail('refresh niet gelukt: exp is niet opgeschoven. open de Grok CLI handmatig');
}

main();
