# cline-exporter

Export **Cline CLI 3.x** sessions to **Markdown** and **JSONL**.

It reads the local files Cline stores under `~/.cline/data/sessions/` and
converts them to Markdown. It does not support the legacy `ui_messages.json`
schema.

## Disclaimer

- **Tested only with Cline CLI** (latest version, 3.x). May not work with earlier versions.
- **For Cline CLI only.** Not designed to work with other tools.
- **Not affiliated:** This project is not affiliated with, approved, or endorsed by Cline, Inc. or any related entity. It is an independent project.

## Requirements

- Python 3.10+

## Quick Start

```bash
# Clean transcript (text only) of all sessions — writes to ./cline_exports/<run>/
python cline_exporter.py export

# Full transcript: text + reasoning + tool calls/results
python cline_exporter.py export --mode full

# Truncate tool results only, keep reasoning untouched
python cline_exporter.py export --mode full --truncate 4000 --truncate-thinking 0

# JSONL dump of all sessions
python cline_exporter.py jsonl

# Only conversation
python cline_exporter.py export --mode full --no-thinking  --no-tools
```

Each `export` run creates its own folder so manifests never overwrite each other.

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

## Output layout (one folder per run)

Every `export` run creates its own folder, so manifests never overwrite each
other:

```
cline_exports/
  2026-11-05_034500_clean/            # <export_timestamp>_<mode>
    manifest.json                     # manifest for this run only
    2026-11-04_195915_my-task_clean/  # <session_timestamp>_<slug>_<mode>
      my-task_clean.md                # <slug>_<mode>.md
  2026-11-05_035120_full/
    manifest.json
    2026-11-04_195915_my-task_full/
      my-task_full.md
```

- Sessions are ordered by `started_at` (newest first).
- The mode appears in the run folder, the session folder and the file name.
- JSONL is global (no mode/filters apply): `./cline_exports/all_sessions.jsonl`.

## Command reference

### `export`

```bash
python cline_exporter.py export [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--limit N` | `all` | Maximum number of sessions (omit for all) |
| `--out DIR` | `./cline_exports` | Base output folder |
| `--mode {clean,full}` | `clean` | Detail level |
| `--no-thinking` | off | `full` only: omit reasoning |
| `--no-tools` | off | `full` only: omit tool calls and results |
| `--no-tool-use` | off | `full` only: omit tool calls |
| `--no-tool-results` | off | `full` only: omit tool results |
| `--truncate N` | `0` | `full` only: default per-part limit; `0` = off |
| `--truncate-thinking N` | `--truncate` | `full` only: limit for thinking |
| `--truncate-tools N` | `--truncate` | `full` only: limit for tool results |
| `--truncate-from {end,start}` | `end` | Side to keep when truncating |
| `--sessions-root PATH` | `~/.cline/data/sessions` | Sessions root |
| `--data-dir PATH` | none | Resolve `<data-dir>/data/sessions` |

### `jsonl`

```bash
python cline_exporter.py jsonl [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--output FILE` | `./cline_exports/all_sessions.jsonl` | Output file |
| `--limit N` | none | Maximum number of sessions |
| `--raw` | off | One line per `<id>.messages.json` (unwrapped) |
| `--sessions-root PATH` | `~/.cline/data/sessions` | Sessions root |
| `--data-dir PATH` | none | Resolve `<data-dir>/data/sessions` |

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

# JSONL with metadata / raw
python cline_exporter.py jsonl
python cline_exporter.py jsonl --raw --output ./cline_exports/raw.jsonl
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

In `clean` mode these flags are ignored (there is no reasoning or tool output).

## Removing parts in `full`

```bash
# No reasoning, keep tool calls/results
python cline_exporter.py export --mode full --no-thinking

# Reasoning only, no tools at all
python cline_exporter.py export --mode full --no-tools

# Keep tool results but drop tool calls
python cline_exporter.py export --mode full --no-tool-use

# Keep tool calls but drop tool results
python cline_exporter.py export --mode full --no-tool-results
```

`--no-tools` is equivalent to `--no-tool-use --no-tool-results`.

## JSONL

```bash
python cline_exporter.py jsonl
```

By default this writes `./cline_exports/all_sessions.jsonl`, one line per
session with metadata:

```json
{"id":"...","name":"...","created":"...","provider":"...","model":"...","cwd":"...","messages":[...]}
```

JSONL always contains the full raw messages, regardless of mode or filters.
Use `--raw` for one line per `<id>.messages.json` file (unwrapped).

## Fidelity metadata

In each `.md` frontmatter:

```yaml
id: "..."
name: "..."
created: "2026-11-04 19:59:15"
messages: 355
export_mode: clean
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
```

`faithful` is `true` only when nothing was truncated **and** nothing was
omitted. `omitted_*` counts parts left out by the mode or `--no-*` flags.

Each run's `manifest.json` records the run folder, mode, include flags,
truncation settings, sessions root, and every session with its output path and
the same fidelity fields.

## Finding the sessions path on Windows

```powershell
$env:USERPROFILE
Get-ChildItem "$env:USERPROFILE\.cline\data\sessions" -Directory
Get-ChildItem "$env:USERPROFILE\.cline\data\sessions" -Recurse -Filter *.messages.json
$env:CLINE_DATA_DIR; $env:CLINE_DIR   # in case of an override
```

## Windows notes

- Paths with `\` work thanks to `pathlib`.
- `started_at` (ISO with `Z`) is parsed correctly on Python 3.10+.
- Folder names are sanitized to avoid invalid characters (`<>:"/\|?*`).

## License

MIT. See the `LICENSE` file.
