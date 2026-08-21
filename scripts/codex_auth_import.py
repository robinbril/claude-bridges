"""Importeer een codex CLI auth.json (ChatGPT OAuth) als CLIProxyAPI
codex-auth. Print alleen afgeleide metadata, nooit tokens.

Multi-account: geef per account het pad naar diens auth.json mee; de
bestandsnaam bevat het e-mailadres, dus accounts overschrijven elkaar niet en
CLIProxyAPI load-balancet automatisch over alle codex-auths in de auth-dir.

    python codex_auth_import.py                     # ~/.codex/auth.json
    python codex_auth_import.py pad/naar/auth.json  # extra account
"""
import base64
import json
import os
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HOME, ".codex", "auth.json")
DST_DIR = os.path.join(HOME, ".cli-proxy-api")


def jwt_payload(tok: str) -> dict:
    part = tok.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


src = json.load(open(SRC, encoding="utf-8"))
tokens = src["tokens"]
idp = jwt_payload(tokens["id_token"])
accp = jwt_payload(tokens["access_token"])
email = idp.get("email") or accp.get("email") or "onbekend"
account_id = tokens.get("account_id") or idp.get(
    "https://api.openai.com/auth", {}).get("chatgpt_account_id", "")
plan = idp.get("https://api.openai.com/auth", {}).get(
    "chatgpt_plan_type", "") or ""
exp = accp.get("exp")
expired = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat() if exp else ""
last_refresh = src.get("last_refresh") or datetime.now(tz=timezone.utc).isoformat()

out = {
    "id_token": tokens["id_token"],
    "access_token": tokens["access_token"],
    "refresh_token": tokens["refresh_token"],
    "account_id": account_id,
    "last_refresh": last_refresh,
    "email": email,
    "type": "codex",
    "expired": expired,
}
naam = "codex-%s%s.json" % (email.strip(), "-" + plan if plan else "")
os.makedirs(DST_DIR, exist_ok=True)
pad = os.path.join(DST_DIR, naam)
json.dump(out, open(pad, "w", encoding="utf-8"), indent=2)
print("GESCHREVEN", naam)
print("email:", email, "| plan:", plan or "-", "| access-token exp:", expired,
      "| last_refresh:", last_refresh)
