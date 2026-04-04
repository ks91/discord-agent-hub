# discord-agent-hub

A new foundation for running shared AI agents for multiple users on Discord.

Goals:

- Use the current OpenAI Responses API
- Support OpenAI, Claude Code, and Gemini CLI in one hub
- Let users define and add agents freely
- Persist sessions and structured event logs
- Keep logs easy to import into `loglm`

## Current Scope

This initial implementation includes:

- Provider-agnostic agent definitions
- Local persistence for sessions, messages, and event logs
- An OpenAI Responses API provider
- An Anthropic Messages API provider
- A Gemini API provider
- Stub providers for Claude Code and Gemini CLI
- A minimal Discord bot that binds one session to one Discord thread

This is still missing or intentionally simplified:

- Full session management for Claude Code and Gemini CLI
- Attachment handling
- Agent management UI
- A `loglm` importer implementation

## Layout

```text
data/
  agents.json              agent definitions
  hub.sqlite3              sessions and messages
  events.jsonl             structured event log
src/discord_agent_hub/
  main.py
  bot.py
  config.py
  models.py
  storage.py
  structured_log.py
  providers/
```

## Development Order

The practical implementation order is:

1. Build the hub around provider APIs first
2. Stabilize session persistence and structured logs
3. Add Discord workflow and agent definitions
4. Add CLI-backed runtimes later as a separate execution layer

That means the first serious provider targets should be:

- OpenAI Responses API
- Anthropic Messages API
- Gemini API

Then, after the hub is stable, add:

- Codex CLI runtime
- Claude Code runtime
- Gemini CLI runtime

The reason is simple: API providers are easier to test, easier to make multi-user safe, and easier to persist cleanly. CLI runtimes are still important, but they introduce process management, permissions, timeouts, resumable sessions, and filesystem/tool execution concerns.

## Testing Strategy

This project should be developed test-first wherever possible.

The recommended approach is:

- Write unit tests first for storage, logging, provider adapters, and message/session routing
- Keep Discord and external provider integration thin
- Mock provider clients at the boundary instead of mocking internal business logic
- Prefer deterministic structured logs and database records over UI-only assertions

The early test focus should be:

- session creation and lookup
- message persistence order
- structured event logging
- provider adapter request/response mapping
- Discord thread to session resolution

## Structured Log Format

Events are written to `data/events.jsonl` as one JSON object per line. Current event types include:

- `session.created`
- `message.user`
- `response.assistant`
- `provider.error`

The intended design is:

- The Discord hub writes a clean research-friendly event stream
- `loglm` gets a dedicated importer that maps these events into whatever downstream view is needed

That is easier to maintain than forcing the bot to mimic raw `loglm` terminal logs directly.

## Setup

1. `python -m venv .venv`
2. `. .venv/bin/activate`
3. `pip install -e .`
4. Copy `.env.example` to `.env`
5. Fill in your Discord and provider credentials
6. Run `python -m discord_agent_hub.main`

### Fast Dev Setup

If you want slash commands to appear quickly during development, set:

- `DEV_GUILD_ID`

When this is set, commands are synced to that guild immediately instead of waiting for global command propagation.

## Discord Flow

- `/agent-list`: shows available agents
- `/chat [agent_id]`: creates a Discord thread and starts a session
- Messages sent inside that thread are routed to the session's provider

### Minimal "Plain LLM" Chat

The fastest way to start is:

1. Set `DISCORD_BOT_TOKEN`
2. Set one provider key:
   `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`
3. Start the bot with `python -m discord_agent_hub.main`
4. In Discord, run `/agent-list`
5. Start a thread with one of:
   `/chat agent_id:openai-default`
   `/chat agent_id:anthropic-default`
   `/chat agent_id:gemini-default`
6. Send messages inside the created thread

You can also run `/hub-status` to confirm which providers are configured.

## Roadmap

- Add attachments and tool handling for OpenAI
- Expand tests around session routing and provider boundaries
- Move agent definitions from JSON-only management toward DB-backed management
- Implement persistent subprocess-backed runtimes for Codex, Claude Code, and Gemini CLI
- Add a `loglm` importer in `loglm` or a companion repository
