#!/usr/bin/env node
/**
 * grok-auth-proxy.js
 * Tiny local shim that lets CCR (or any OpenAI-compatible client) talk to the
 * Grok Build subscription endpoint (cli-chat-proxy.grok.com) without an API
 * key. It reuses the Grok CLI's logged-in session:
 *   - reads the OAuth JWT fresh from ~/.grok/auth.json on every request, so a
 *     token refreshed by the grok CLI is picked up automatically,
 *   - injects the x-grok-client-version header the proxy requires,
 *   - forwards the request (streaming included) to cli-chat-proxy.grok.com.
 *
 * CONCURRENCY GATE (choke point). grok.com's cli-chat-proxy returns
 * "Server error mid-response" when too many heavy agentic streams hit it at
 * once (e.g. a delegate.sh "grok army"). This is the single point every grok
 * path flows through (delegate, roundtable, LiteLLM router), so the gate lives
 * here: at most GROK_MAX_CONCURRENCY requests reach grok.com simultaneously,
 * the rest queue (FIFO) and drain as slots free. No caller can overload the
 * rail again, regardless of how many workers it launches.
 *   GROK_MAX_CONCURRENCY  max in-flight upstream requests (default 2)
 *   GROK_MAX_QUEUE        max queued requests before 503 (default 200)
 *   GROK_QUEUE_WAIT_MS    max time a request waits in queue (default 180000)
 */
'use strict';

const http = require('http');
const https = require('https');
const fs = require('fs');
const os = require('os');
const path = require('path');

const GROK_DIR = path.join(os.homedir(), '.grok');
const UPSTREAM_HOST = 'cli-chat-proxy.grok.com';
const PORT = Number(process.env.GROK_PROXY_PORT || 3457);
const CONNECT_TIMEOUT_MS = Number(process.env.GROK_SOCKET_TIMEOUT_MS || 300000);

const MAX_CONCURRENCY = Math.max(1, Number(process.env.GROK_MAX_CONCURRENCY || 30));
const MAX_QUEUE = Math.max(1, Number(process.env.GROK_MAX_QUEUE || 400));
const QUEUE_WAIT_MS = Math.max(1000, Number(process.env.GROK_QUEUE_WAIT_MS || 300000));

// Dedicated upstream agent. maxCachedSessions:0 zet TLS session-resumption uit:
// hergebruik van een TLS-sessie onder concurrency is de oorzaak van "bad record
// mac" (SSL alert 20). Ruime sockets zodat de gate (niet de socketpool) de rem is.
// Hoogste redeneerstand voor Grok 4.6. Overschrijfbaar met GROK_REASONING_EFFORT.
const REASONING_EFFORT = process.env.GROK_REASONING_EFFORT || 'high';

const upstreamAgent = new https.Agent({
  keepAlive: true,
  maxSockets: 64,
  maxFreeSockets: 16,
  timeout: 300000,
  maxCachedSessions: 0,
});

function readJson(p) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (_) { return null; }
}

// The JWT lives under the single "https://auth.x.ai::<clientId>" key in auth.json.
function currentAuth() {
  const auth = readJson(path.join(GROK_DIR, 'auth.json')) || {};
  const key = Object.keys(auth)[0];
  const jwt = key ? (auth[key].key || '') : '';
  const ver = (readJson(path.join(GROK_DIR, 'version.json')) || {}).version || '';
  return { jwt, ver };
}

// Passieve rate-limit capture voor het usage-dashboard (gethrottled).
const USAGE_SNAPSHOT = path.join(__dirname, 'grok-usage.json');
let lastUsageWrite = 0;
function captureRateLimits(headers) {
  const limTok = Number(headers['x-ratelimit-limit-tokens']);
  if (!Number.isFinite(limTok) || limTok <= 0) return;
  const now = Date.now();
  if (now - lastUsageWrite < 5000) return;
  lastUsageWrite = now;
  const snap = {
    updated_at: now,
    limit_tokens: limTok,
    remaining_tokens: Number.isFinite(Number(headers['x-ratelimit-remaining-tokens'])) ? Number(headers['x-ratelimit-remaining-tokens']) : null,
    limit_requests: Number(headers['x-ratelimit-limit-requests']) || null,
    remaining_requests: Number(headers['x-ratelimit-remaining-requests']) || null,
  };
  fs.writeFile(USAGE_SNAPSHOT, JSON.stringify(snap), () => {});
}

// ── Concurrency gate ────────────────────────────────────────────────────
let active = 0;
const queue = []; // { run, reject, done, timer }

function pump() {
  while (queue.length && active < MAX_CONCURRENCY) {
    const item = queue.shift();
    if (item.done) continue;
    item.done = true;
    if (item.timer) clearTimeout(item.timer);
    active++;
    item.run();
  }
}

function acquire(run, reject) {
  if (active < MAX_CONCURRENCY) { active++; run(); return; }
  if (queue.length >= MAX_QUEUE) { reject('queue_full'); return; }
  const item = { run, reject, done: false, timer: null };
  item.timer = setTimeout(() => {
    if (item.done) return;
    item.done = true;
    const i = queue.indexOf(item);
    if (i >= 0) queue.splice(i, 1);
    reject('queue_timeout');
  }, QUEUE_WAIT_MS);
  queue.push(item);
}

function release() {
  if (active > 0) active--;
  pump();
}

// Bind exactly one release to a response's lifecycle (success, error, abort).
function releaseOnceFor(res) {
  let released = false;
  const rel = () => { if (released) return; released = true; release(); };
  res.once('close', rel);
  res.once('finish', rel);
  return rel;
}

// ── Actual upstream proxy for one request ──────────────────────────────
function startProxy(clientReq, clientRes) {
  releaseOnceFor(clientRes); // frees the slot whenever this response ends

  // Read the JWT at execution time (post-queue) so a token refreshed while we
  // waited is picked up.
  const { jwt, ver } = currentAuth();
  if (!jwt) {
    clientRes.writeHead(401, { 'content-type': 'application/json' });
    clientRes.end(JSON.stringify({ error: 'grok-auth-proxy: no Grok CLI session found in ~/.grok/auth.json. Run the grok CLI once to log in.' }));
    return;
  }

  const upstream = https.request(
    {
      host: UPSTREAM_HOST,
      port: 443,
      agent: upstreamAgent,
      method: clientReq.method,
      path: clientReq.url,
      timeout: CONNECT_TIMEOUT_MS,
      headers: {
        ...clientReq.headers,
        host: UPSTREAM_HOST,
        authorization: `Bearer ${jwt}`,
        'x-grok-client-version': ver,
        'x-client-version': ver,
      },
    },
    (upRes) => {
      captureRateLimits(upRes.headers);
      if (upRes.statusCode === 401) {
        clientRes.writeHead(401, { 'content-type': 'application/json' });
        upRes.resume();
        clientRes.end(JSON.stringify({ error: 'grok-auth-proxy: Grok weigerde de subscription-JWT (verlopen?). Open de grok CLI even zodat die het token refresht in ~/.grok/auth.json, en probeer opnieuw.' }));
        return;
      }
      clientRes.writeHead(upRes.statusCode || 502, upRes.headers);
      upRes.pipe(clientRes);
    },
  );

  upstream.on('timeout', () => {
    upstream.destroy(new Error(`connect timeout after ${CONNECT_TIMEOUT_MS}ms`));
  });
  upstream.on('error', (err) => {
    if (clientRes.headersSent) { clientRes.destroy(); return; }
    clientRes.writeHead(502, { 'content-type': 'application/json' });
    clientRes.end(JSON.stringify({ error: `grok-auth-proxy upstream error: ${err.message}` }));
  });

  // If the client hangs up before we finish, tear down the upstream too.
  clientRes.once('close', () => { if (!upstream.destroyed) upstream.destroy(); });

  // Grok 4.6 denkt alleen als je er expliciet om vraagt: zonder reasoning_effort
  // komt er geen reasoning_content terug. De harness stuurt dat veld nooit mee,
  // dus injecteren we het hier. Alleen op JSON-bodies van chat-calls; al het
  // andere (health, models, niet-JSON) gaat ongewijzigd door zoals voorheen.
  const isChat = /\/chat\/completions|\/messages/.test(clientReq.url || '');
  const isJson = /application\/json/i.test(clientReq.headers['content-type'] || '');
  if (!isChat || !isJson) {
    clientReq.pipe(upstream);
    return;
  }

  const stukken = [];
  let bytes = 0;
  clientReq.on('data', (c) => { stukken.push(c); bytes += c.length; });
  clientReq.on('end', () => {
    let body = Buffer.concat(stukken);
    try {
      const j = JSON.parse(body.toString('utf8'));
      if (j && typeof j === 'object' && !Array.isArray(j) && !j.reasoning_effort) {
        j.reasoning_effort = REASONING_EFFORT;
        body = Buffer.from(JSON.stringify(j), 'utf8');
      }
    } catch (_) {
      // Onparseerbaar: ongewijzigd doorsturen is altijd beter dan weigeren.
    }
    upstream.setHeader('content-length', Buffer.byteLength(body));
    upstream.end(body);
  });
  clientReq.on('error', () => { if (!upstream.destroyed) upstream.destroy(); });
}

const server = http.createServer((clientReq, clientRes) => {
  // Health check: never gated, no upstream call.
  if (clientReq.method === 'GET' && clientReq.url === '/health') {
    const { jwt, ver } = currentAuth();
    clientRes.writeHead(200, { 'content-type': 'application/json' });
    clientRes.end(JSON.stringify({
      ok: true, hasJwt: Boolean(jwt), clientVersion: ver || null,
      gate: { active, queued: queue.length, max: MAX_CONCURRENCY },
    }));
    return;
  }

  acquire(
    () => startProxy(clientReq, clientRes),
    (why) => {
      clientReq.resume(); // drain the request body we won't forward
      clientRes.writeHead(503, { 'content-type': 'application/json', 'retry-after': '5' });
      clientRes.end(JSON.stringify({ error: `grok-auth-proxy: concurrency gate ${why} (max ${MAX_CONCURRENCY}, queue ${MAX_QUEUE})` }));
    },
  );
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`grok-auth-proxy listening on http://127.0.0.1:${PORT} -> https://${UPSTREAM_HOST} (gate: max ${MAX_CONCURRENCY} concurrent, queue ${MAX_QUEUE})`);
});
