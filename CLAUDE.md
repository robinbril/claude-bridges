# claude-bridges — instructies voor Claude

Deze repo laat de volledige Claude Code harness op andere breinen draaien via
een lokale bridge. Lees eerst de README voor de architectuur.

## Eerste kennismaking: draai de intake

Als de gebruiker deze repo net heeft gekregen of om setup vraagt, ga dan NIET
zelf een configuratie gokken. Draai eerst een gebundelde intake (heb je een
batch-grill-skill zoals /batch-grill-me, gebruik die; anders stel je de vragen
gebundeld via je vraag-tool). Vraag minimaal:

1. **Abonnementen en keys.** Welke heb je: ChatGPT (Plus/Pro/Team), Grok
   (subscription of API), OpenRouter-key, Cursor, Claude (Pro/Max, hoeveel
   seats)? Subscription of API-key maakt uit: sommige modellen (bv.
   gpt-5.6-sol) werken alleen via API-key, niet via een ChatGPT-account.
2. **Modellen per rail.** Welk model wil je op de grok-rail, welke keten op de
   codex-rail, welk goedkoop model op openrouter?
3. **Auto-verdeling.** Welke procentuele verdeling wil je over de rails
   (AUTO_SPLIT), bv. 65/35 of 50/50, en waarom (kwaliteit vs. quota)?
4. **Budget.** Hoe strak zit je op tokens/quota per abonnement? Dat bepaalt
   of de dure modellen default zijn of alleen voor eindoordeel.

## Na de intake: bevestig en adviseer

Vat de keuzes samen ("je hebt gekozen voor ...", benoem of dat een handige
verdeling is en waarom) en genereer daaruit `~/.delegate.conf` op basis van
`delegate/delegate.conf.example`. Geef daarna een concreet
orkestratie-advies, aangepast aan hun abonnementen. Default-aanbeveling:

- **Zwaar oordeel en grote, belangrijke feedback**: gpt-5.6-sol op medium
  (vereist API-key; op een ChatGPT-account is gpt-5.6-terra het hoogste).
- **Planning en architectuur**: Claude Fable op medium of low effort.
- **Subagents en uitvoerend werk**: situationeel via de auto-rail; bulk- en
  leeswerk naar de goedkoopste rail (openrouter), frontend-werk niet naar
  grok-klasse modellen.
- **Eindverificatie**: een native Claude-seat, nooit een bridge-rail.

Sluit af met de drie handmatige stappen die alleen de gebruiker kan doen:
inloggen bij de betreffende CLI's (grok CLI, codex CLI of device-login,
eventueel cursor-agent login) en `MODEL_ROUTER_KEY` zetten.

## Harde regels

- Geen PII-, klant- of medische data over externe rails (grok, openrouter,
  codex, cursor). Dat werk blijft op de eigen Claude-seats.
- Keys en tokens horen in `~/.delegate.conf` of de omgeving, nooit in git.
- Machine-specifieke paden horen in `~/.delegate.conf`, niet in de scripts.
