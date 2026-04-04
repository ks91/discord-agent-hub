# discord-agent-hub Repository Notes

## Scope
This file describes repository-specific conventions for developing `discord-agent-hub`.

## Project Direction
- Keep implementation API-first.
- Treat CLI runtimes as a later layer unless the task explicitly requires them.

## Local Environment
- Use the local virtual environment in `./.venv/` when running Python commands.
- Preferred test command:
  - `.venv/bin/python -m pytest -q`
- Preferred syntax/bytecode check:
  - `.venv/bin/python -m compileall src`

## Testing Rule
- Develop test-first where practical.
- Add or update unit tests before or alongside code changes.
- Prefer testing storage, logging, provider mapping, and routing logic without live Discord or live API calls.
- Mock provider boundaries instead of mocking internal business logic.

## Data and Secrets
- Do not commit `.env`.
- Do not commit `.loglm_agent`.
- Treat `data/` as local runtime state unless the user explicitly wants fixtures or samples committed.
- `logs/` is for local logs and should stay untracked.

## Agent and Provider Conventions
- Agent definitions currently bootstrap from `data/agents.json`.
- Keep provider implementations thin and stateless where possible.
- Keep conversation state in hub storage, not inside provider-specific hidden state, unless a runtime explicitly requires it.
- New providers should ship with focused request/response mapping tests.

## Discord Conventions
- Keep Discord command handlers thin.
- Prefer moving logic into testable helper functions or storage/provider layers.
- Use `DEV_GUILD_ID` for fast command sync during development.

## Escalation-First Rule
- If a required command fails due to permissions or sandbox restrictions, retry with escalated execution first.
- Do not switch to a different implementation path before trying the same command with approval.
- Use alternatives only if escalation is rejected or escalated execution still fails.
