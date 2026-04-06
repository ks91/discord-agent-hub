# discord-agent-hub

A new foundation for running shared AI agents for multiple users on Discord.

Goals:

- Use the current OpenAI, Anthropic, and Gemini APIs
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
- A minimal Discord bot that binds one session to one Discord thread

This is still missing or intentionally simplified:

- Attachment handling
- Agent management UI
- A `loglm` importer implementation
- Optional cloud-side tools such as web search and code execution

## Layout

```text
data/
  agents.json              agent definitions
  hub.sqlite3              sessions and messages
  events.jsonl             structured event log
examples/
  *.md                     import-ready agent definitions
src/discord_agent_hub/
  main.py
  bot.py
  config.py
  models.py
  storage.py
  structured_log.py
  providers/
```

## Architecture

The current architecture is API-first:

- OpenAI Responses API
- Anthropic Messages API
- Gemini API

The current product direction is to focus on API providers and cloud-side tools first. Local CLI runtimes are possible in principle, but they are not a current priority because their effects are tied to the machine running the hub itself.

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
- `/agent-import`: imports an agent from a Markdown file with a ```agent block
- `/agent-show`: shows the imported agent definition
- `/agent-delete`: deletes an agent definition after confirmation
- `/chat [agent_id]`: creates a Discord thread and starts a session
- Messages sent inside that thread are routed to the session's provider

## Image Attachments

Image attachments are currently supported for OpenAI, Anthropic, and Gemini chat agents.

- Images are stored in local session history
- Only image attachments are supported for now
- When sending conversation history back to providers, only the most recent user image is re-sent

This keeps research logs complete while avoiding oversized multimodal requests caused by repeatedly re-sending older images.

### Minimal "Plain LLM" Chat

The fastest way to start is:

1. Set `DISCORD_BOT_TOKEN`
2. Set one provider key:
   `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`
3. Start the bot with `python -m discord_agent_hub.main`
4. In Discord, run `/agent-list`
5. Start a thread with one of:
   `/chat agent_id:gpt-default`
   `/chat agent_id:claude-default`
   `/chat agent_id:gemini-default`
6. Send messages inside the created thread

You can also run `/hub-status` to confirm which providers are configured.

Sample import-ready agent files are available under `examples/`.
If an imported agent already exists, re-run `/agent-import` with `overwrite:true` to replace it.

## Agent Management

The current agent workflow is:

- Create: import a new Markdown file with `/agent-import`
- Inspect: use `/agent-show`
- Update: re-import the same agent with `/agent-import overwrite:true`
- Delete: use `/agent-delete`

This keeps agent definitions file-based and versionable, which fits long instruction prompts better than trying to manage everything through short slash-command arguments.

## Roadmap

- Add optional provider-side tools such as web search and code execution
- Add attachments and tool handling for OpenAI
- Move agent definitions from JSON-only management toward DB-backed management
- Add a `loglm` importer in `loglm` or a companion repository
