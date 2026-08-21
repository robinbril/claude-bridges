$env:CLAUDE_CONFIG_DIR = Join-Path $env:USERPROFILE '.claude-grok'
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8317"
$env:ANTHROPIC_API_KEY = $env:MODEL_ROUTER_KEY
claude --model grok @args
