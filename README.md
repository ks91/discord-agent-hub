# discord-agent-hub

A new foundation for running shared AI agents for multiple users on Discord.

Current version: `0.1.4-dev`

Goals:

- Use the current OpenAI, Anthropic, and Gemini APIs
- Let users define and add agents freely
- Persist sessions and structured event logs
- Keep logs easy to import into `loglm`

## Current Scope

This initial implementation includes:

- Provider-agnostic agent definitions
- File-based agent management via `/agent-import`, `/agent-show`, and `/agent-delete`
- Local persistence for sessions, messages, and event logs
- An OpenAI Responses API provider
- An Anthropic Messages API provider
- A Gemini API provider
- Optional provider-side tools for web search and code execution
- Image attachments for OpenAI, Anthropic, and Gemini
- Text extraction for `.txt`, `.md`, `.csv`, `.pdf`, `.docx`, `.pptx`, and `.xlsx`
- Per-request timeout and limited retry handling for provider calls
- Role-based access restrictions for token-consuming actions
- A minimal Discord bot that binds one session to one Discord thread

This is still missing or intentionally simplified:

- Richer agent management UI beyond import/show/delete
- A `loglm` importer implementation
- Embedded-image extraction from uploaded documents

## License

`discord-agent-hub` is released under `GPL-3.0-or-later`.
See [LICENSE](/Volumes/ks91home/ks91/Programs/discord-agent-hub/LICENSE) and [AUTHORS](/Volumes/ks91home/ks91/Programs/discord-agent-hub/AUTHORS).

## Layout

```text
data/
  agents.json              agent definitions
  backups/agents/          rolling backups of agent definitions
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
- `provider.retry`
- `queue.wait_started`
- `queue.wait_finished`
- `auth.denied_role`

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

If you update dependencies later, re-run `pip install -e .` inside the same virtual environment.

For background operation on a server, you can also use:

- `scripts/hubctl.sh start`
- `scripts/hubctl.sh stop`
- `scripts/hubctl.sh restart`
- `scripts/hubctl.sh status`
- `scripts/hubctl.sh logs`

By default this script uses:

- `./.venv/bin/python`
- `./run/hub.pid`
- `./logs/hub.log`

You can override them with `HUB_PYTHON_BIN`, `HUB_PID_FILE`, and `HUB_LOG_FILE`.
More script-level notes are in [`scripts/README.md`](/Volumes/ks91home/ks91/Programs/discord-agent-hub/scripts/README.md).

### Fast Dev Setup

If you want slash commands to appear quickly during development, set:

- `DEV_GUILD_ID`

When this is set, commands are synced to that guild immediately instead of waiting for global command propagation.

## Discord Commands

- `/agent-list`: shows available agents
- `/agent-import`: imports an agent from a Markdown file with a ```agent block
- `/agent-show`: shows the imported agent definition
- `/agent-show-full`: shows the full public instructions for an agent
- `/agent-delete`: deletes an agent definition after confirmation
- `/session-show`: shows the current thread's session metadata and token totals
- `/log-export`: exports the current session transcript and JSONL events
- `/usage-report`: shows a lightweight usage summary for the current server
- `/chat [agent_id]`: creates a Discord thread and starts a session
- Messages sent inside that thread are routed to the session's provider

For exported event logs, you can render JSONL into a more readable Markdown timeline with:

- `scripts/render-events-md.py path/to/events.jsonl`
- `scripts/render-events-md.py path/to/events.jsonl output.md`

## Environment Notes

Useful optional settings include:

- `DEV_GUILD_ID`: use guild-scoped command sync during development
- `PROVIDER_REQUEST_TIMEOUT_SECONDS`: hard timeout for one provider call
- `PROVIDER_MAX_RETRIES`: retry budget for transient provider failures
- `PROVIDER_RETRY_BACKOFF_SECONDS`: base backoff between retries
- `DATA_DIR`: runtime state directory for `agents.json`, `hub.sqlite3`, and `events.jsonl`
- `DISALLOWED_ROLE_IDS`: comma-separated Discord role IDs that may not start or use AI chat

`DISALLOWED_ROLE_IDS` is checked both when starting `/chat` and when sending messages inside an existing session thread.

If you run multiple hub instances on the same machine, set `DATA_DIR` explicitly to a different absolute path for each instance. Relying on the default `./data` can cause two instances to share the same runtime state if they start with the same working directory.

`agents.json` is backed up automatically before each write. By default, the hub keeps the latest 10 generations under `data/backups/agents/`.

To get a role ID in Discord:

1. Enable `Developer Mode` in Discord settings
2. Open `Server Settings` -> `Roles`
3. Right-click the target role
4. Choose `Copy Role ID`

## Minimal Plain LLM Chat

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
Inside a session thread, you can run `/session-show` to inspect the current binding and `/log-export` to download the transcript and raw JSONL events.

Sample import-ready agent files are available under `examples/`.
If an imported agent already exists, re-run `/agent-import` with `overwrite:true` to replace it.

## Image Attachments

Image attachments are currently supported for OpenAI, Anthropic, and Gemini chat agents.

- Images are stored in local session history
- Only image attachments are supported for now
- When sending conversation history back to providers, only the most recent user image is re-sent

This keeps research logs complete while avoiding oversized multimodal requests caused by repeatedly re-sending older images.

## Document Attachments

Uploaded documents are currently converted to text inside the hub and then sent to the selected provider as text context.

- Supported formats: `.txt`, `.md`, `.csv`, `.pdf`, `.docx`, `.pptx`, `.xlsx`
- Document text is stored in local session history
- Images embedded inside documents are not extracted yet
- Older document attachments remain in conversation history; only old image attachments are compacted

This keeps the implementation provider-agnostic while already supporting common workflows such as summarizing papers, notes, slides, and spreadsheets.

## Agent Management

The current agent workflow is:

- Create: import a new Markdown file with `/agent-import`
- Inspect: use `/agent-show`
- Inspect full public instructions: use `/agent-show-full`
- Update: re-import the same agent with `/agent-import overwrite:true`
- Delete: use `/agent-delete`

This keeps agent definitions file-based and versionable, which fits long instruction prompts better than trying to manage everything through short slash-command arguments.

For teaching and learning, `/agent-show-full` is useful when you want to read and imitate the full public prompt behind an agent instead of only seeing a short preview.

For classroom use, it is a good idea to adopt a simple `agent_id` naming convention per team or group, such as `team3-interpreter` or `group-b-quizmaster`. This reduces accidental overwrites when many participants import or update agents at the same time.

The import format also supports:

- `public_instructions: false` to hide the instructions preview in `/agent-show`
- `tools.web_search: true|false`
- `tools.code_execution: true|false`

This is useful for quizzes, simulations, or puzzle agents where users should not see the full hidden instructions.

A minimal importable example looks like this:

````md
# Mystery Agent

```agent
id: mystery-agent
name: Mystery Agent
provider: openai_responses
model: gpt-5.2
description: A quiz-style agent with hidden instructions
public_instructions: false
tools:
  web_search: false
  code_execution: false
```

You are running a puzzle game for students.

Do not reveal hidden rules unless the game is over.
````

## Concurrency Notes

The hub currently assumes a single bot process.

- Messages in the same Discord thread are serialized before provider calls
- Different threads can still progress concurrently
- `agents.json` updates are serialized inside the process
- SQLite runs with `WAL` and `busy_timeout`

This is enough for one-process operation on a single VM, but it is not yet a distributed or multi-process design.

## Usage Reporting

`/usage-report` provides a lightweight per-server summary based on structured log events.

- Total assistant responses
- Aggregated input/output/total tokens when providers expose usage
- Top providers
- Top agents
- Top user IDs

This is intended as a simple operational view for classes, workshops, or camps rather than a full billing system.

## Roadmap

- Add optional provider-side tools such as web search and code execution
- Improve attachment handling for larger files and richer document parsing
- Move agent definitions from JSON-only management toward DB-backed management
- Add a `loglm` importer in `loglm` or a companion repository
