#!/usr/bin/env python3
"""Export Cline CLI 3.x sessions to Markdown and JSONL.

Reads `<sessions_root>/<id>/<id>.messages.json` plus `<id>.json` metadata,
orders sessions by local `started_at`, and writes one folder per run.

Usage:
    python cline_exporter.py export
    python cline_exporter.py export --mode full
    python cline_exporter.py export --mode full --no-thinking
    python cline_exporter.py export --mode full --no-tools --truncate 4000
    python cline_exporter.py export --mode full --truncate 4000 --truncate-from start
    python cline_exporter.py jsonl
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from cline_sessions import (
    DEFAULT_SESSIONS_ROOT,
    discover_sessions,
    resolve_data_dir_sessions,
    slugify,
)
from cline_markdown import MODES, TRUNCATE_FROM, session_to_markdown

DEFAULT_OUTPUT_DIR = "cline_exports"
DEFAULT_JSONL = "cline_exports/all_sessions.jsonl"

_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def _session_metadata(session):
    extra = session.extra_meta
    return {
        "id": session.id,
        "name": session.name,
        "created": session.created_str(),
        "message_count": len(session.messages),
        "provider": session.provider,
        "model": session.model,
        "cwd": session.cwd,
        "status": extra.get("status"),
        "ended_at": extra.get("ended_at"),
        "exit_code": extra.get("exit_code"),
        "interactive": extra.get("interactive"),
        "source": extra.get("source"),
        "workspace_root": extra.get("workspace_root"),
        "team_name": extra.get("team_name"),
        "enable_tools": extra.get("enable_tools"),
        "enable_spawn": extra.get("enable_spawn"),
        "enable_teams": extra.get("enable_teams"),
        "parent_session_id": extra.get("parent_session_id"),
        "parent_agent_id": extra.get("parent_agent_id"),
        "agent_id": extra.get("agent_id"),
        "is_subagent": extra.get("is_subagent"),
        "cline_title": extra.get("cline_title"),
        "git_branch": extra.get("git_branch"),
        "checkpoint_latest": extra.get("checkpoint_latest"),
        "session_origin": extra.get("session_origin"),
        "usage": session.usage,
        "aggregate_usage": session.aggregate_usage,
        "total_cost": session.total_cost,
        "aggregated_agents_cost": session.aggregated_agents_cost,
        "models": session.models,
    }


def _compute_totals(sessions):
    totals = {
        "sessions": len(sessions),
        "messages": 0,
        "sessions_with_usage": 0,
        "sessions_with_cost": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_cost": None,
    }
    cost = 0.0
    for info in sessions:
        totals["messages"] += info.get("message_count") or 0
        usage = info.get("usage")
        if usage:
            totals["sessions_with_usage"] += 1
            for key in _TOKEN_KEYS:
                value = usage.get(key)
                if isinstance(value, (int, float)):
                    totals[key] += int(value)
        total_cost = info.get("total_cost")
        if isinstance(total_cost, (int, float)):
            cost += float(total_cost)
            totals["sessions_with_cost"] += 1
    if totals["sessions_with_cost"]:
        totals["total_cost"] = cost
    return totals


def _unique_dir(base, name):
    folder = base / name
    if not folder.exists():
        return folder
    index = 2
    while True:
        candidate = base / f"{name}_{index}"
        if not candidate.exists():
            return candidate
        index += 1


def _unique_session_folder(run_root, ts_prefix, name_slug, mode, sid):
    folder = run_root / f"{ts_prefix}_{name_slug}_{mode}"
    if not folder.exists():
        return folder
    return run_root / f"{ts_prefix}_{name_slug}_{mode}_{slugify(sid, 8)}"


def export_session(
    session,
    run_root,
    mode,
    include_thinking,
    include_tool_use,
    include_tool_results,
    truncate_thinking,
    truncate_tools,
    truncate_from,
):
    created = session.created
    ts_prefix = created.strftime("%Y-%m-%d_%H%M%S") if created else "no-date"
    name_slug = slugify(session.name)
    folder = _unique_session_folder(
        run_root, ts_prefix, name_slug, mode, session.id
    )
    folder.mkdir(parents=True, exist_ok=True)
    out_file = folder / f"{name_slug}_{mode}.md"

    markdown, fidelity = session_to_markdown(
        session,
        mode=mode,
        include_thinking=include_thinking,
        include_tool_use=include_tool_use,
        include_tool_results=include_tool_results,
        truncate_thinking=truncate_thinking,
        truncate_tools=truncate_tools,
        truncate_from=truncate_from,
    )
    out_file.write_text(markdown, encoding="utf-8")

    info = _session_metadata(session)
    info["file"] = str(out_file)
    info.update(fidelity)
    return info


def export_all(
    sessions_root,
    limit,
    out_root,
    mode,
    include_thinking,
    include_tool_use,
    include_tool_results,
    truncate_thinking,
    truncate_tools,
    truncate_from,
):
    base = Path(out_root) if out_root else Path.cwd() / DEFAULT_OUTPUT_DIR
    base.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_root = _unique_dir(base, f"{run_ts}_{mode}")
    run_root.mkdir(parents=True, exist_ok=True)

    sessions = discover_sessions(sessions_root, limit=limit)
    manifest = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "run_folder": str(run_root),
        "mode": mode,
        "include_thinking": include_thinking,
        "include_tool_use": include_tool_use,
        "include_tool_results": include_tool_results,
        "truncate_thinking": truncate_thinking,
        "truncate_tools": truncate_tools,
        "truncate_from": truncate_from,
        "sessions_root": str(sessions_root),
        "sessions": [],
    }
    for session in sessions:
        try:
            manifest["sessions"].append(
                export_session(
                    session,
                    run_root,
                    mode,
                    include_thinking,
                    include_tool_use,
                    include_tool_results,
                    truncate_thinking,
                    truncate_tools,
                    truncate_from,
                )
            )
        except OSError as exc:
            print(f"Error exporting {session.id}: {exc}", file=sys.stderr)

    manifest["totals"] = _compute_totals(manifest["sessions"])

    manifest_file = run_root / "manifest.json"
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _raw_jsonl_lines(sessions_root, limit):
    root = Path(sessions_root)
    files = sorted(root.rglob("*.messages.json"))
    if limit:
        files = files[:limit]
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Skipping {path}: {exc}", file=sys.stderr)
            continue
        yield json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _wrapped_jsonl_lines(sessions_root, limit):
    sessions = discover_sessions(sessions_root, limit=limit)
    for session in sessions:
        payload = _session_metadata(session)
        payload["messages"] = session.messages
        yield json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def write_jsonl(output_file, lines):
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(output_file, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line)
            fh.write("\n")
            count += 1
    return count


def _resolve_sessions_root(args):
    if args.data_dir:
        return resolve_data_dir_sessions(args.data_dir)
    return Path(args.sessions_root)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Export Cline CLI sessions to Markdown and JSONL"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--sessions-root",
        default=str(DEFAULT_SESSIONS_ROOT),
        help="Root folder of Cline sessions",
    )
    common.add_argument(
        "--data-dir",
        default=None,
        help="Cline data dir; resolves the sessions folder automatically",
    )

    p_export = sub.add_parser(
        "export",
        parents=[common],
        help="Export sessions to Markdown folders",
    )
    p_export.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of sessions (default: all)",
    )
    p_export.add_argument(
        "--out",
        default=None,
        help=f"Output base folder (default: ./{DEFAULT_OUTPUT_DIR})",
    )
    p_export.add_argument("--mode", choices=MODES, default="clean")
    p_export.add_argument(
        "--no-thinking",
        action="store_true",
        help="full only: omit reasoning (thinking)",
    )
    p_export.add_argument(
        "--no-tools",
        action="store_true",
        help="full only: omit tool calls and tool results",
    )
    p_export.add_argument(
        "--no-tool-use",
        action="store_true",
        help="full only: omit tool calls",
    )
    p_export.add_argument(
        "--no-tool-results",
        action="store_true",
        help="full only: omit tool results",
    )
    p_export.add_argument(
        "--truncate",
        type=int,
        default=0,
        help="full only: default max chars per truncatable part; 0 disables",
    )
    p_export.add_argument(
        "--truncate-thinking",
        type=int,
        default=None,
        help="full only: max chars per thinking part (overrides --truncate)",
    )
    p_export.add_argument(
        "--truncate-tools",
        type=int,
        default=None,
        help="full only: max chars per tool_result part (overrides --truncate)",
    )
    p_export.add_argument(
        "--truncate-from",
        choices=TRUNCATE_FROM,
        default="end",
        help="Keep the start (end) or the end (start) of truncated parts",
    )

    p_jsonl = sub.add_parser(
        "jsonl",
        parents=[common],
        help="Write sessions to a JSONL file",
    )
    p_jsonl.add_argument(
        "--output",
        default=DEFAULT_JSONL,
        help=f"Output file (default: ./{DEFAULT_JSONL})",
    )
    p_jsonl.add_argument("--limit", type=int, default=None)
    p_jsonl.add_argument(
        "--raw",
        action="store_true",
        help="One line per <id>.messages.json file (no metadata wrapper)",
    )

    return parser


def _resolve_export_options(args):
    if args.mode == "clean":
        ignored = (
            args.no_thinking
            or args.no_tools
            or args.no_tool_use
            or args.no_tool_results
            or args.truncate
            or args.truncate_thinking is not None
            or args.truncate_tools is not None
        )
        if ignored:
            print(
                "Note: clean mode exports text only; "
                "truncation and omission flags are ignored.",
                file=sys.stderr,
            )
        return False, False, False, 0, 0

    include_thinking = not args.no_thinking
    include_tool_use = not (args.no_tools or args.no_tool_use)
    include_tool_results = not (args.no_tools or args.no_tool_results)
    truncate_thinking = (
        args.truncate_thinking
        if args.truncate_thinking is not None
        else args.truncate
    )
    truncate_tools = (
        args.truncate_tools
        if args.truncate_tools is not None
        else args.truncate
    )
    if not include_thinking:
        truncate_thinking = 0
    if not include_tool_results:
        truncate_tools = 0
    return (
        include_thinking,
        include_tool_use,
        include_tool_results,
        truncate_thinking,
        truncate_tools,
    )


def main():
    parser = build_parser()
    args = parser.parse_args()
    sessions_root = _resolve_sessions_root(args)

    if args.command == "export":
        (
            include_thinking,
            include_tool_use,
            include_tool_results,
            truncate_thinking,
            truncate_tools,
        ) = _resolve_export_options(args)
        manifest = export_all(
            sessions_root,
            args.limit,
            args.out,
            args.mode,
            include_thinking,
            include_tool_use,
            include_tool_results,
            truncate_thinking,
            truncate_tools,
            args.truncate_from,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    elif args.command == "jsonl":
        if args.raw:
            lines = _raw_jsonl_lines(sessions_root, args.limit)
        else:
            lines = _wrapped_jsonl_lines(sessions_root, args.limit)
        count = write_jsonl(args.output, lines)
        print(
            json.dumps(
                {"output": args.output, "sessions": count}, ensure_ascii=False
            )
        )


if __name__ == "__main__":
    main()
