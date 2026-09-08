# claude-bridges

Read docs/bridge-guide.html before changing or installing this project.

## Setup

Ask for the subscription seats/providers and the explicit model for each requested rail. Use the installed provider catalog; do not assume model availability from examples. Ask for task limits and whether additional context or MCP access is needed. Generate ~/.delegate.conf from delegate/delegate.conf.example only after those choices are known.

## Execution contract

- No automatic routing or fallback. A model change starts a separately authorized task.
- A failure ends the current attempt. Resume requires DELEGATE_TASK_ID and DELEGATE_RESUME=1 with the original contract.
- A completed CLI result is not proof that the requested work passed acceptance. Verification remains a separate step.
- CLI tool access is not an operating-system sandbox. Native Cursor uses its own permissions and cannot accept Claude allowedTools.
- Sensitive tasks stay on the active authorized subscription seat. DELEGATE_SENSITIVE=1 blocks all external rails, including Cursor. Regex checks cover explicitly supplied context, not arbitrary future tool reads.
- Credentials and task transcripts stay local and outside git. Do not modify active services or publish without the user's authorization.

## Validation

Use fixture CLI processes and local HTTP servers for regression tests. Do not spend subscription tokens to test retries, error paths or queue handling. Live provider compatibility is a separate validation step.
