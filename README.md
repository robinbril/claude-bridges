# claude-bridges

Draai de volledige Claude Code harness (tools, MCP, subagents) op andere
breinen via een lokale bridge. Een CLIProxyAPI-instance op `:8317` vertaalt
het Anthropic-protocol naar drie rails:

| Rail | Brein | Effort | Auth | Kosten |
|---|---|---|---|---|
| `auto` | 65% grok / 35% codex (Bresenham-teller) | per rail | n.v.t. | subscription |
| `grok` | Grok 4.6 (Build) | xhigh | Grok CLI subscription-JWT via `grok-auth-proxy.js` | subscription |
| `codex` | gpt-5.6-terra > luna | high | ChatGPT-subscription OAuth (native provider) | subscription |
| `openrouter` | DeepSeek V4 (of elk OpenRouter-model) | default | API-key | per token |
| `cursor` | cursor-agent CLI (eigen agent-loop, geen claude-harness) | n.v.t. | Cursor-login | subscription |
| `<seat>` | native claude op een eigen seat (`SEAT_<NAAM>` in de conf) | seat | Claude-login | subscription |

Alle machine-specifieke instellingen (key, paden, verdeling, modellen,
seats, context-injectie) leven in `~/.delegate.conf`; kopieer
`delegate/delegate.conf.example` en pas aan. De scripts zelf zijn generiek.

**Setup met Claude:** geef deze repo aan Claude Code en vraag om setup.
`CLAUDE.md` laat Claude eerst een intake draaien (abonnementen, modellen,
verdeling, budget) en genereert daarna jouw `~/.delegate.conf` plus een
orkestratie-advies.

`delegate.sh` is de rail-laag eromheen: headless `claude -p` met allowedTools,
context-injectie per repo, retry op mid-stream drops, fallback grok naar
openrouter, en jsonl-logging per run.

## Architectuur

```
claude -p  (volledige harness)
   | ANTHROPIC_BASE_URL=http://127.0.0.1:8317
   v
CLIProxyAPI :8317  (config.yaml, auth-dir ~/.cli-proxy-api)
   |-- openai-compatibility "grokbridge" -> grok-auth-proxy.js :3457 -> cli-chat-proxy.grok.com
   |-- openai-compatibility "openrouter" -> openrouter.ai
   `-- native codex-provider -> ChatGPT-backend (OAuth-tokens in auth-dir)
```

De grok-auth-proxy leest het subscription-JWT per request vers uit
`~/.grok/auth.json` en bevat een concurrency-gate (default 2 gelijktijdige
streams) omdat grok.com bij meer parallelle agentic streams mid-response
dropt. Alle grok-paden lopen door dit ene choke point.

## Installatie

1. Download CLIProxyAPI (getest: 7.2.93) en zet `cli-proxy-api.exe` in een
   eigen map met `cliproxy/config.example.yaml` als `config.yaml`. Vul de
   router-key (zelf verzinnen) en eventueel de OpenRouter-key in.
2. Grok-rail: log in met de Grok CLI (JWT in `~/.grok/auth.json`) en start
   `grok-auth-proxy/grok-auth-proxy.js` met node op `:3457`.
3. Codex-rail: log eenmalig in met de Codex CLI (tokens in
   `~/.codex/auth.json`) en draai `scripts/codex_auth_import.py`. Dat zet de
   OAuth-tokens om naar het CLIProxyAPI auth-formaat in `~/.cli-proxy-api/`.
   Alternatief zonder Codex CLI: `cli-proxy-api.exe -codex-device-login`.
4. Start de bridge: `cli-proxy-api.exe -config config.yaml`, of gebruik
   `scripts/bridge-ensure.ps1` (idempotent, herstart alleen een dode bridge).
5. Kopieer `delegate/delegate.conf.example` naar `~/.delegate.conf`, zet
   daar minimaal `MODEL_ROUTER_KEY` (de router-key uit stap 1), en gebruik
   `delegate/delegate.sh`.
6. Autostart (optioneel): `scripts/install-autostart.ps1` registreert een
   logon-task die `scripts/stack-ensure.ps1` draait. Dat script brengt de
   hele stack idempotent up (bridge, grok-auth-proxy, en de optionele
   claude-code-router/litellm/embed-server als die poorten geconfigureerd
   zijn); een service die al luistert wordt overgeslagen. Pas de paden in
   stack-ensure.ps1 aan je eigen layout aan.

## Gebruik

```bash
# default rail (auto): 65/35 verdeeld over grok en codex
bash delegate.sh auto "Read Grep Glob Bash" prompt.txt

# expliciete rail overruled de verdeling
bash delegate.sh grok "Read Edit Write Bash" prompt.txt
bash delegate.sh codex "Read Edit Write Bash" prompt.txt

# openrouter (DeepSeek), bulk-leeswerk
bash delegate.sh openrouter "Read Grep Glob" prompt.txt

# cursor-agent (Windows-host of WSL-fallback), model als 4e argument
bash delegate.sh cursor "" prompt.txt gpt-5.6

# effort per call; DELEGATE_DRY=1 print alleen de auto-keuze
DELEGATE_EFFORT=medium bash delegate.sh grok "Read" prompt.txt
```

`DELEGATE_CWD=<repo>` laat de agent in die repo starten (CLAUDE.md en
repo-skills laden mee). `DELEGATE_MCP=1` zet MCP-discovery aan (default uit
voor snelle starts).

## Meerdere accounts

CLIProxyAPI load-balancet native over alle auth-bestanden in de auth-dir
(`~/.cli-proxy-api`). Voor codex betekent dat: importeer per account een
auth.json en de bridge verdeelt de load en failovert automatisch bij
quota-uitputting van een account.

```bash
# account 1 (default codex CLI login)
python scripts/codex_auth_import.py

# account 2: log met de codex CLI in onder een ander profiel
# (CODEX_HOME=~/.codex-acc2 codex login) en importeer dat bestand
python scripts/codex_auth_import.py ~/.codex-acc2/auth.json
```

Zonder codex CLI kan elk extra account ook direct via
`cli-proxy-api.exe -codex-device-login` (code invoeren op een ingelogde
browser van dat account). Sturing van de verdeling: `routing.strategy`
(round-robin of fill-first) en `session-affinity` in config.yaml.

## Valkuilen (elk echt gebeurd)

- **Grok-JWT verloopt.** Alleen de Grok CLI kan hem refreshen; de proxy meldt
  het expliciet. Open de CLI even en de rail doet het weer.
- **Cooldown-vergrendeling.** Een enkele quota-403 zette een provider
  permanent in cooldown tot herstart. Daarom `disable-cooling: true` op beide
  openai-compatibility providers.
- **Claude-ID aliassen.** `claude -p` stuurt interne Anthropic-model-IDs
  (small-fast, subagents). Zonder aliassen in config.yaml 502't elke
  delegatie. De aliassen wijzen naar grok; de codex-rail zet daarom
  `ANTHROPIC_SMALL_FAST_MODEL` expliciet, zodat hij niet van de grok-rail
  afhangt.
- **Mid-stream drops met exit 0.** De bridge sluit soms mid-response en de
  harness geeft toch exit 0. `run_met_retry` in delegate.sh scant de output
  op drop-markers en herstart tot 4x.
- **Codex-tokenrefresh.** CLIProxyAPI refresht geimporteerde codex-tokens
  zelf (core auth auto-refresh, 15 min interval); de import is eenmalig.
- **gpt-5.6-sol weigert op een ChatGPT-account.** "Not supported when using
  Codex with a ChatGPT account"; sol is API-key-only. De codex-keten valt
  daarom terug op terra > luna.

## Sensitiviteit

Geen PII-, klant- of medische data over grok, openrouter, codex of cursor.
Dat werk blijft op de eigen Claude-seats.
