# cline-exporter

Export **Cline CLI 3.x** sessions to **Markdown** and **JSONL**.

It reads the local files Cline stores under `~/.cline/data/sessions/` and
converts them to Markdown. It does not support the legacy `ui_messages.json`
schema.

## Requirements

- Python 3.10+

## Quick Start

```bash
# Conversation transcript (dialogue only) of all sessions — writes to ./exports/<run>/
python cline_exporter.py export

# Prompts only (user prompts without assistant responses)
python cline_exporter.py export --mode prompts

# Full transcript: text + reasoning + tool calls/results
python cline_exporter.py export --mode full

# Full transcript plus the complete JSONL archive
python cline_exporter.py export --mode full --jsonl

# Truncate tool results only, keep reasoning untouched
python cline_exporter.py export --mode full --truncate 4000 --truncate-thinking 0

# JSONL dump of all sessions
python cline_exporter.py jsonl
```

Each `export` run creates its own folder so manifests never overwrite each other.

## Disclaimer

- **Tested only with Cline CLI** (latest version, 3.x). May not work with earlier versions.
- **For Cline CLI only.** Not designed to work with other tools.
- **Not affiliated:** This project is not affiliated with, approved, or endorsed by Cline, Inc. or any related entity. It is an independent project.


## Command reference

### Export

```bash
python cline_exporter.py export [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--limit N` | `all` | Maximum number of sessions (omit for all) |
| `--out DIR` | `./exports` | Base output folder |
| `--jsonl` | off | Also write `all_sessions.jsonl` with full raw session data |
| `--mode {conversation,full,prompts}` | `conversation` | Detail level |
| `--no-thinking` | off | `full` only: omit reasoning |
| `--no-tools` | off | `full` only: omit tool calls and results |
| `--no-tool-use` | off | `full` only: omit tool calls |
| `--no-tool-results` | off | `full` only: omit tool results |
| `--truncate N` | `0` | `full` only: default per-part limit; `0` = off |
| `--truncate-thinking N` | `--truncate` | `full` only: limit for thinking |
| `--truncate-tools N` | `--truncate` | `full` only: limit for tool results |
| `--truncate-from {end,start}` | `end` | Side to keep when truncating |
| `--dry-run` | off | Simulate run without creating folders or writing files |
| `--quiet` | off | Suppress verbose output |
| `--sessions-root PATH` | `~/.cline/data/sessions` | Sessions root |
| `--data-dir PATH` | none | Resolve `<data-dir>/data/sessions` |
| `--sessions IDS` | none | Comma-separated list of session IDs to select |
| `--since YYYY-MM-DD` | none | Filter sessions created on or after date |
| `--filter TEXT` | none | Filter sessions matching text in title or ID |

### Preflight Check

```bash
# Human-readable check
python cline_exporter.py --check

# Machine-readable JSON output for launchers and automation
python cline_exporter.py --check --json
```

### List Sessions

```bash
# Text table listing
python cline_exporter.py --list-sessions

# Machine-readable JSON listing
python cline_exporter.py --list-sessions --json

# Filter and sort
python cline_exporter.py --list-sessions --limit 10 --since 2026-11-01 --order created-desc
```

### Exit Codes

- `0`: Success / ready (data found).
- `1`: Error (missing source directory, unreadable file, runtime failure).
- `2`: Invalid CLI usage or arguments.
- `3`: No data (sessions directory found but empty, or filters matched 0 sessions).

## Examples

```bash
# Clean transcript (text only) of all sessions
python cline_exporter.py export

# Limit to the latest 20 sessions
python cline_exporter.py export --limit 20

# Full transcript: text + reasoning + tool calls/results
python cline_exporter.py export --mode full

# Full without reasoning
python cline_exporter.py export --mode full --no-thinking

# Full without tools
python cline_exporter.py export --mode full --no-tools

# Truncate tools but not reasoning
python cline_exporter.py export --mode full --truncate 4000 --truncate-thinking 0

# Truncate reasoning but not tools
python cline_exporter.py export --mode full --truncate 4000 --truncate-tools 0

# Keep the end instead of the beginning of truncated parts
python cline_exporter.py export --mode full --truncate 4000 --truncate-from start

# From an isolated data dir
python cline_exporter.py export --data-dir ./my_project_history

# JSONL with metadata / raw (each writes its own run folder)
python cline_exporter.py jsonl
python cline_exporter.py jsonl --raw
```

## Truncation (`full` only)

Truncation is **disabled by default** (`0` = no limit). Enable it explicitly and
per part type:

```bash
# Truncate both reasoning and tool results to 4000 chars
python cline_exporter.py export --mode full --truncate 4000

# Truncate tool results only, leave reasoning untouched
python cline_exporter.py export --mode full --truncate 4000 --truncate-thinking 0

# Truncate reasoning only, leave tool results untouched
python cline_exporter.py export --mode full --truncate 4000 --truncate-tools 0

# Different limits per type
python cline_exporter.py export --mode full --truncate-thinking 1000 --truncate-tools 8000
```

How the flags interact:

- `--truncate N` sets the default limit for both truncatable types.
- `--truncate-thinking N` overrides the limit for `thinking` parts.
- `--truncate-tools N` overrides the limit for `tool_result` parts.
- A value of `0` disables truncation for that type.
- `--truncate-from {end,start}` controls which side is kept:
  - `end` (default): keep the beginning, drop the end.
  - `start`: keep the end, drop the beginning.

Limits are **per part**, not a global budget. `text` and the `tool_use` input
are never truncated. Every cut is marked in the body with the number of removed
characters:

```
[... truncated 7755 chars ...]
```

In `conversation` and `prompts` modes these flags are ignored (there is no reasoning or tool output).

## Removing parts in `full`

```bash
# No reasoning, keep tool calls/results
python cline_exporter.py export --mode full --no-thinking

# Reasoning only, no tools at all
python cline_exporter.py export --mode full --no-tools

# Dialogue-only Markdown using full mode's omission flags
python cline_exporter.py export --mode full --no-thinking --no-tools

# Keep tool results but drop tool calls
python cline_exporter.py export --mode full --no-tool-use

# Keep tool calls but drop tool results
python cline_exporter.py export --mode full --no-tool-results
```

`--no-tools` is equivalent to `--no-tool-use --no-tool-results`.
The last command produces the same dialogue-only Markdown content as
`--mode conversation`; add `--jsonl` only when you also want the complete raw
session archive in that bundle.

## JSONL

```bash
python cline_exporter.py jsonl [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--out DIR` | `./exports` | Base output folder |
| `--limit N` | none | Maximum number of sessions |
| `--raw` | off | One line per `<id>.messages.json` (unwrapped) |
| `--sessions-root PATH` | `~/.cline/data/sessions` | Sessions root |
| `--data-dir PATH` | none | Resolve `<data-dir>/data/sessions` |

Each `jsonl` run creates its own `<out>/<timestamp>_jsonl/` folder with
`manifest.json` and `all_sessions.jsonl` (`all_sessions_raw.jsonl` with
`--raw`).

`export` writes Markdown and a manifest by default. Add `--jsonl` to include
`all_sessions.jsonl` in that same run bundle. The standalone `jsonl` command
creates its own `<out>/<timestamp>_jsonl/` run folder.
Each line contains one session with metadata, usage, models, and the full raw
messages:

```json
{"id":"...","name":"...","created":"...","message_count":355,"provider":"cline","model":"cline-free/nemotron-3.5-super-vl","cwd":"...","source":"cli","source_root":"...","source_format":"cline-cli-3.x","status":"failed","ended_at":"...","exit_code":1,"usage":{"input_tokens":15107058,"output_tokens":28055,"cache_read_tokens":13771136,"cache_write_tokens":0,"total_cost":0.0},"aggregate_usage":null,"total_cost":0.0,"aggregated_agents_cost":0.0,"models":[...],"messages":[...]}
```

JSONL always contains the full raw messages, regardless of mode or filters.
`usage`, `total_cost` and `models` come from the session metadata, not from the
Markdown filters. `aggregate_usage` is populated only when it differs from
`usage` (for example, when subagents are involved); otherwise it is `null`.

Use `--raw` for one line per `<id>.messages.json` file (unwrapped), written to
`all_sessions_raw.jsonl` inside the run folder. In that case only the raw
messages are written, so session-level usage/cost is not included (it lives in
the sibling `<id>.json`).

## Layout read from Cline CLI 3.x

```
~/.cline/data/sessions/
  <id>/
    <id>.json            # metadata: started_at, prompt, provider, model, cwd...
    <id>.messages.json   # { messages: [ { role, ts, content: [...] } ] }
```

On Windows: `C:\Users\<YourUser>\.cline\data\sessions\`.

Each message has `role` (`user`/`assistant`), `ts`, and `content` as a list of
parts: `text`, `thinking`, `tool_use`, `tool_result`.

`<id>.json` also carries `status`, `ended_at`, `exit_code`, `workspace_root`,
`team_name`, `enable_*`, and a `metadata` object with `usage`, `aggregateUsage`,
`totalCost`, `aggregatedAgentsCost`, `git`, `checkpoint`, `title`, and
`sessionHistoryOrigin`.

Assistant messages may include `modelInfo` (`id`, `provider`, `family`) and
`metrics` (`inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`).

## Output layout (one folder per run)

Every `export` run creates its own folder, so manifests never overwrite each
other:

```
exports/
  2026-11-05_034500_conversation/            # <export_timestamp>_<mode>
    manifest.json                            # manifest for this run only
    2026-11-04_195915_my-task_conversation/  # <session_timestamp>_<slug>_<mode>
      my-task_conversation.md                # <slug>_<mode>.md
  2026-11-05_035120_full/                    # add --jsonl to include JSONL here
    manifest.json
    all_sessions.jsonl                       # only with --jsonl
    2026-11-04_195915_my-task_full/
      my-task_full.md
  2026-11-05_041000_jsonl/            # standalone `jsonl` run
    manifest.json
    all_sessions.jsonl
```

- Sessions are ordered by `started_at` (newest first).
- The mode appears in the run folder, the session folder and the file name.
- JSONL is opt-in for Markdown exports (`--jsonl`); the standalone `jsonl`
  command always creates a JSONL-only run bundle.

## Fidelity metadata

In each `.md` frontmatter:

```yaml
id: "..."
name: "..."
created: "2026-11-04 19:59:15"
messages: 355
export_mode: conversation
faithful: false
include_thinking: false
include_tool_use: false
include_tool_results: false
truncated: false
truncated_parts: 0
truncated_thinking: 0
truncated_tools: 0
truncate_thinking: 0
truncate_tools: 0
truncate_from: end
omitted_parts: 528
omitted_thinking: 164
omitted_tool_use: 182
omitted_tool_results: 182
omitted_other: 0
source_format: "cline-cli-3.x"
provider: "cline"
model: "cline-free/nemotron-3.5-super-vl"
cwd: "C:\\...\\my-project"
status: "failed"
ended_at: "2026-11-05T03:07:50.546Z"
usage:
  input_tokens: 15107058
  output_tokens: 28055
  cache_read_tokens: 13771136
  cache_write_tokens: 0
  total_cost: 0.0
models:
  - id: "cline-free/nemotron-3.5-super-vl"
    provider: "cline"
    family: "nemotron"
    messages: 165
    input_tokens: 15107058
    output_tokens: 28055
    cache_read_tokens: 13771136
    cache_write_tokens: 0
```

`faithful` is `true` only when nothing was truncated **and** nothing was
omitted. `omitted_*` counts parts left out by the mode or `--no-*` flags.

`usage` is taken from Cline's `metadata.usage` (falling back to the sum of the
per-message `metrics` if needed). `total_cost` is Cline's own accounting from
`metadata.totalCost`; the exporter **does not recalculate costs**. A cost of
`0` means Cline reported zero (for example, a free model), while `null` means
the value is unknown.

`models` is aggregated from the assistant messages' `modelInfo`. Sessions that
switch models during the conversation get one entry per model, with the number
of messages and tokens for each. Cost is only available per session, so it is
not split per model.

If the session uses subagents, `usage` covers this session's messages while
`metadata.aggregateUsage` may include subagent sessions stored in other
folders. In that case the manifest and JSONL include `aggregate_usage` and
`aggregated_agents_cost` separately.

Each run's `manifest.json` records the run folder, source, JSONL file, mode,
include flags, truncation settings, sessions root, and every session with its
output path, source/source_root, the same fidelity fields, usage/cost/models,
and the curated Cline metadata
(`status`, `ended_at`, `workspace_root`, `team_name`, `git_branch`,
`checkpoint_latest`, `session_origin`, subagent fields). It also includes a
`totals` object with session/message counts and summed tokens/cost.



## Finding the sessions path on Windows

```powershell
$env:USERPROFILE
Get-ChildItem "$env:USERPROFILE\.cline\data\sessions" -Directory
Get-ChildItem "$env:USERPROFILE\.cline\data\sessions" -Recurse -Filter *.messages.json
$env:CLINE_DATA_DIR; $env:CLINE_DIR   # in case of an override
```

## Using with VS Code Extension (Optional)

If the extension stores sessions in the same SDK layout, point
`--sessions-root` at its sessions folder. Candidate locations:

- Windows: `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline\data\sessions\`
- macOS: `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline/data/sessions/`
- Linux: `~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline/data/sessions/`

The old `tasks/<id>/ui_messages.json` layout is not supported.

Although designed for Cline CLI, you can export your VS Code Cline sessions by pointing to the VS Code global storage directory:

```bash
# Example for macOS VS Code sessions
python cline_exporter.py export --sessions-root "~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline/data/sessions"
```

## Windows notes

- Paths with `\` work thanks to `pathlib`.
- `started_at` (ISO with `Z`) is parsed correctly on Python 3.10+.
- Folder names are sanitized to avoid invalid characters (`<>:"/\|?*`).

## Roadmap

- [ ] **Test with the VS Code extension.** It has not been tested, but it
  should work when the extension stores sessions in the same SDK format
  (`<data-dir>/sessions/<id>/<id>.json` + `<id>.messages.json`). Point
  `--sessions-root` at the extension's sessions folder to try it.
- [ ] **Test on macOS.** The macOS paths are documented but untested.
- [ ] **Legacy extension layout.** The old
  `tasks/<id>/ui_messages.json` schema is not supported.

## License

MIT. See the `LICENSE` file.
