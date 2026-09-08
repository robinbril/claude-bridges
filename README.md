# claude-bridges

Run Claude Code with an explicitly selected model through CLIProxyAPI, or use a native subscription seat. Cursor uses its own CLI.

The runner records each task locally, validates structured completion, and resumes only when explicitly requested. It never switches models or replays a failed task automatically. Context is deduplicated and bounded; reported token usage stays separate from unknown usage and from verification of the work.

[Installation, configuration, limits and recovery](docs/bridge-guide.html)

Python 3.10+, Git Bash (for delegate.sh), and the relevant authenticated CLI are required. Bridge routes require an existing CLIProxyAPI installation and a configured model listed by its model catalog. The Grok proxy uses Node.js built-ins.

Local checks: Python unittest discovery under tests and Node's test runner on tests/test-proxy.cjs. Tests use fixture CLIs and local HTTP servers, without model calls.
