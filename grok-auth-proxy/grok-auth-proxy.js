#!/usr/bin/env node
'use strict';
const https = require('https');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { configuration, createProxy } = require('./proxy-server');

const config = configuration(process.env);
const grokDir = path.join(os.homedir(), '.grok');
const host = 'cli-chat-proxy.grok.com';
const agent = new https.Agent({ keepAlive: true, maxSockets: config.concurrency,
  maxFreeSockets: config.concurrency, timeout: config.timeoutMs, maxCachedSessions: 0 });

function readJson(filename) {
  try { return JSON.parse(fs.readFileSync(filename, 'utf8')); }
  catch { return null; }
}

function auth() {
  const data = readJson(path.join(grokDir, 'auth.json')) || {};
  const entry = data[Object.keys(data)[0]];
  return { jwt: entry?.key || '', ver: readJson(path.join(grokDir, 'version.json'))?.version || '' };
}

function headerNumber(value) {
  if (value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

let lastUsageWrite = 0;
function capture(headers) {
  const limit = headerNumber(headers['x-ratelimit-limit-tokens']);
  const now = Date.now();
  if (limit === null || now - lastUsageWrite < 5000) return;
  lastUsageWrite = now;
  const snapshot = { updated_at: now, limit_tokens: limit,
    remaining_tokens: headerNumber(headers['x-ratelimit-remaining-tokens']),
    limit_requests: headerNumber(headers['x-ratelimit-limit-requests']),
    remaining_requests: headerNumber(headers['x-ratelimit-remaining-requests']) };
  fs.writeFile(path.join(__dirname, 'grok-usage.json'), JSON.stringify(snapshot), error => {
    if (error) console.error('Could not persist rate-limit snapshot:', error.code);
  });
}

if (require.main === module) {
  const server = createProxy(config, { host, auth, capture,
    request: (options, callback) => https.request({ ...options, host, port: 443, agent }, callback) });
  server.listen(config.port, '127.0.0.1', () => {
    console.log(`grok-auth-proxy: 127.0.0.1:${config.port}, concurrency=${config.concurrency}`);
  });
}

module.exports = { headerNumber };
