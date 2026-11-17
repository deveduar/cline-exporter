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
    python cline_exporter.py export --dry-run
    python cline_exporter.py jsonl
    python cline_exporter.py --check --json
    python cline_exporter.py --list-sessions --json
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from cline_sessions import (
    DEFAULT_SESSIONS_ROOT,
    count_sessions,
    discover_sessions,
    filter_sessions,
    resolve_data_dir_sessions,
    slugify,
)
from cline_markdown import MODES, TRUNCATE_FROM, session_to_markdown

VERSION = "1.0.0"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NO_DATA = 3

DEFAULT_OUTPUT_DIR = "exports"
JSONL_FILENAME = "all_sessions.jsonl"
RAW_JSONL_FILENAME = "all_sessions_raw.jsonl"

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
        "revision": _source_revision(session),
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
        "source_root": str(session.folder.parent),
        "source_format": "cline-cli-3.x",
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


def _source_revision(session):
    """Cheap revision token from Cline's message and session metadata files."""
    revision = []
    for source_path in (session.messages_file, session.meta_file):
        try:
            source_stat = source_path.stat()
            revision.append((source_stat.st_mtime_ns, source_stat.st_size))
        except OSError:
            revision.append(None)
    return revision


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
    write_jsonl_file=False,
    dry_run=False,
    session_ids=None,
    since=None,
    filter_text=None,
):
    sessions = discover_sessions(sessions_root)
    if session_ids or since or filter_text or limit:
        sessions = filter_sessions(
            sessions,
            last=limit,
            since=since,
            session_ids=session_ids,
            filter_text=filter_text,
        )

    if dry_run:
        sessions_meta = [_session_metadata(s) for s in sessions]
        totals = _compute_totals(sessions_meta)
        manifest = {
            "tool": "cline_exporter",
            "version": VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "run_folder": None,
            "dry_run": True,
            "writes": False,
            "source": "cli",
            "mode": mode,
            "include_thinking": include_thinking,
            "include_tool_use": include_tool_use,
            "include_tool_results": include_tool_results,
            "truncate_thinking": truncate_thinking,
            "truncate_tools": truncate_tools,
            "truncate_from": truncate_from,
            "sessions_root": str(sessions_root),
            "session_count": len(sessions),
            "sessions": [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "created": s["created"],
                    "message_count": s["message_count"],
                    "model": s["model"],
                    "status": s["status"],
                }
                for s in sessions_meta
            ],
            "totals": totals,
        }
        return manifest

    base = Path(out_root) if out_root else Path.cwd() / DEFAULT_OUTPUT_DIR
    base.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_root = _unique_dir(base, f"{run_ts}_{mode}")
    run_root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "tool": "cline_exporter",
        "version": VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "run_folder": str(run_root),
        "source": "cli",
        "mode": mode,
        "include_thinking": include_thinking,
        "include_tool_use": include_tool_use,
        "include_tool_results": include_tool_results,
        "truncate_thinking": truncate_thinking,
        "truncate_tools": truncate_tools,
        "truncate_from": truncate_from,
        "sessions_root": str(sessions_root),
        "session_count": len(sessions),
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

    if write_jsonl_file:
        jsonl_file = run_root / JSONL_FILENAME
        write_jsonl(jsonl_file, (_session_jsonl_line(s) for s in sessions))
        manifest["jsonl_file"] = str(jsonl_file)
    else:
        manifest["jsonl_file"] = None

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


def _session_jsonl_line(session):
    payload = _session_metadata(session)
    payload["messages"] = session.messages
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _wrapped_jsonl_lines(sessions_root, limit):
    sessions = discover_sessions(sessions_root, limit=limit)
    for session in sessions:
        yield _session_jsonl_line(session)


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


def export_jsonl(
    sessions_root,
    limit,
    out_root,
    raw=False,
    dry_run=False,
    session_ids=None,
    since=None,
    filter_text=None,
):
    sessions = discover_sessions(sessions_root)
    if session_ids or since or filter_text or limit:
        sessions = filter_sessions(
            sessions,
            last=limit,
            since=since,
            session_ids=session_ids,
            filter_text=filter_text,
        )

    if dry_run:
        manifest = {
            "tool": "cline_exporter",
            "version": VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "run_folder": None,
            "dry_run": True,
            "writes": False,
            "source": "cli",
            "raw": raw,
            "sessions_root": str(sessions_root),
            "limit": limit,
            "jsonl_file": None,
            "session_count": len(sessions),
        }
        return manifest

    base = Path(out_root) if out_root else Path.cwd() / DEFAULT_OUTPUT_DIR
    base.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_root = _unique_dir(base, f"{run_ts}_jsonl")
    run_root.mkdir(parents=True, exist_ok=True)

    filename = RAW_JSONL_FILENAME if raw else JSONL_FILENAME
    output_file = run_root / filename
    if raw:
        if session_ids or since or filter_text or limit:
            files = [s.messages_file for s in sessions if s.messages_file.exists()]
            lines = []
            for path in files:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    lines.append(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
                except (json.JSONDecodeError, OSError) as exc:
                    print(f"Skipping {path}: {exc}", file=sys.stderr)
        else:
            lines = list(_raw_jsonl_lines(sessions_root, limit))
    else:
        lines = [_session_jsonl_line(s) for s in sessions]

    count = write_jsonl(output_file, lines)

    manifest = {
        "tool": "cline_exporter",
        "version": VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "run_folder": str(run_root),
        "source": "cli",
        "raw": raw,
        "sessions_root": str(sessions_root),
        "limit": limit,
        "jsonl_file": str(output_file),
        "session_count": count,
    }
    (run_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def check_preflight(sessions_root, as_json=False):
    root = Path(sessions_root)
    source_exists = root.exists()
    is_dir = root.is_dir() if source_exists else False

    if not source_exists or not is_dir:
        res = {
            "tool": "cline_exporter",
            "version": VERSION,
            "status": "error",
            "ok": False,
            "sessions_root": str(sessions_root),
            "session_count": None,
            "no_data_reason": None,
            "error": f"Sessions root directory does not exist or is not a directory: {sessions_root}",
            "writes": False,
        }
        if as_json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"Error: {res['error']}", file=sys.stderr)
        return EXIT_ERROR

    try:
        count = count_sessions(root)
    except OSError as exc:
        res = {
            "tool": "cline_exporter",
            "version": VERSION,
            "status": "error",
            "ok": False,
            "sessions_root": str(sessions_root),
            "session_count": None,
            "no_data_reason": None,
            "error": str(exc),
            "writes": False,
        }
        if as_json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if count == 0:
        res = {
            "tool": "cline_exporter",
            "version": VERSION,
            "status": "no_data",
            "ok": True,
            "sessions_root": str(sessions_root),
            "session_count": 0,
            "no_data_reason": f"No Cline sessions found in {sessions_root}",
            "error": None,
            "writes": False,
        }
        if as_json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"Sessions root: {sessions_root} (0 sessions found)")
        return EXIT_NO_DATA

    res = {
        "tool": "cline_exporter",
        "version": VERSION,
        "status": "ready",
        "ok": True,
        "sessions_root": str(sessions_root),
        "session_count": count,
        "no_data_reason": None,
        "error": None,
        "writes": False,
    }
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"Sessions root: {sessions_root} ({count} session(s) found)")
    return EXIT_OK


def list_sessions_cmd(
    sessions_root,
    last=None,
    since=None,
    session_ids=None,
    filter_text=None,
    order="created-desc",
    as_json=False,
):
    root = Path(sessions_root)
    if not root.is_dir():
        if as_json:
            print(
                json.dumps(
                    {
                        "tool": "cline_exporter",
                        "version": VERSION,
                        "ok": False,
                        "status": "error",
                        "source": {
                            "resolved": str(sessions_root),
                            "exists": False,
                            "origin": "cli",
                        },
                        "sessions_found": 0,
                        "error": f"Sessions directory not found: {sessions_root}",
                        "writes": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"Error: Sessions directory not found: {sessions_root}", file=sys.stderr)
        return EXIT_ERROR

    sessions = discover_sessions(root)
    filtered = filter_sessions(
        sessions,
        last=last,
        since=since,
        session_ids=session_ids,
        filter_text=filter_text,
        order=order,
    )

    if as_json:
        session_list = []
        for s in filtered:
            session_list.append(
                {
                    "id": s.id,
                    "title": s.name,
                    "created": s.created.isoformat() if s.created else None,
                    "updated": s.updated.isoformat() if s.updated else None,
                    "revision": _source_revision(s),
                    "message_count": len(s.messages),
                    "model": s.model,
                    "cwd": s.cwd,
                    "status": s.extra_meta.get("status"),
                    "source_file": str(s.messages_file),
                }
            )
        res = {
            "tool": "cline_exporter",
            "version": VERSION,
            "ok": True,
            "status": "ready" if session_list else "no_data",
            "source": {
                "resolved": str(sessions_root),
                "exists": True,
                "origin": "cli",
            },
            "sessions_found": len(session_list),
            "no_data_reason": "No sessions matched selection" if not session_list else None,
            "writes": False,
            "sessions": session_list,
        }
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        if not filtered:
            print("Sessions found: 0")
        else:
            print(f"Sessions found: {len(filtered)}")
            for s in filtered:
                dt_str = s.created_str()
                msg_count = len(s.messages)
                print(f"{dt_str}  {s.id:<36}  {s.name}  [{msg_count} msgs]")

    return EXIT_OK if filtered else EXIT_NO_DATA


def _resolve_sessions_root(args):
    if getattr(args, "data_dir", None):
        return resolve_data_dir_sessions(args.data_dir)
    return Path(args.sessions_root)


def build_parser():
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
    common.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate run without writing output directories or files",
    )
    common.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output during export",
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="Format output as JSON for check or list",
    )
    common.add_argument(
        "--limit",
        "--last",
        dest="limit",
        type=int,
        default=None,
        help="Maximum number of sessions (default: all)",
    )
    common.add_argument(
        "--sessions",
        default=None,
        help="Comma-separated session IDs to filter",
    )
    common.add_argument(
        "--since",
        default=None,
        help="Filter sessions created on or after date (YYYY-MM-DD)",
    )
    common.add_argument(
        "--filter",
        default=None,
        help="Filter sessions by text in title or ID",
    )
    common.add_argument(
        "--order",
        choices=["created-desc", "created-asc", "updated-desc", "updated-asc"],
        default="created-desc",
        help="Ordering for listing sessions",
    )

    parser = argparse.ArgumentParser(
        description="Export Cline CLI sessions to Markdown and JSONL",
        parents=[common],
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run preflight check and exit",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List sessions and exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )

    sub = parser.add_subparsers(dest="command", required=False)

    p_export = sub.add_parser(
        "export",
        parents=[common],
        help="Export sessions to Markdown folders",
    )
    p_export.add_argument(
        "--out",
        default=None,
        help=f"Output base folder (default: ./{DEFAULT_OUTPUT_DIR})",
    )
    p_export.add_argument(
        "--jsonl",
        action="store_true",
        help="Also write all_sessions.jsonl with full raw session data",
    )
    p_export.add_argument("--mode", choices=MODES, default="conversation")
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
        help="Write sessions to a JSONL run folder",
    )
    p_jsonl.add_argument(
        "--out",
        default=None,
        help=f"Output base folder (default: ./{DEFAULT_OUTPUT_DIR})",
    )
    p_jsonl.add_argument(
        "--raw",
        action="store_true",
        help="One line per <id>.messages.json file (no metadata wrapper)",
    )

    sub.add_parser(
        "check",
        parents=[common],
        help="Perform preflight check",
    )
    sub.add_parser(
        "list",
        parents=[common],
        help="List sessions",
    )

    return parser


def _resolve_export_options(args):
    if args.mode in ("conversation", "prompts"):
        ignored = (
            args.no_thinking
            or args.no_tools
            or args.no_tool_use
            or args.no_tool_results
            or args.truncate
            or args.truncate_thinking is not None
            or args.truncate_tools is not None
        )
        if ignored and not getattr(args, "quiet", False):
            print(
                f"Note: {args.mode} mode exports text only; "
                "truncation and omission flags are ignored.",
                file=sys.stderr,
            )
        return False, False, False, 0, 0

    if args.mode == "tool-results":
        truncate_tools = (
            args.truncate_tools
            if args.truncate_tools is not None
            else args.truncate
        )
        return False, False, True, 0, truncate_tools

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

    if args.check or args.command == "check":
        sys.exit(check_preflight(sessions_root, as_json=args.json))

    if args.list_sessions or args.command == "list":
        sys.exit(
            list_sessions_cmd(
                sessions_root,
                last=args.limit,
                since=args.since,
                session_ids=args.sessions,
                filter_text=args.filter,
                order=args.order,
                as_json=args.json,
            )
        )

    if not args.command:
        parser.print_help(sys.stderr)
        sys.exit(EXIT_USAGE)

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
            write_jsonl_file=args.jsonl,
            dry_run=args.dry_run,
            session_ids=args.sessions,
            since=args.since,
            filter_text=args.filter,
        )
        count = manifest.get("session_count", len(manifest.get("sessions", [])))
        if args.json:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        elif not getattr(args, "quiet", False):
            if args.dry_run:
                print(
                    f"[dry-run] {count} session(s) found ({args.mode}); no files written"
                )
            else:
                run_folder = manifest.get("run_folder")
                print(f"Exported {count} session(s) ({args.mode}) to: {run_folder}")

        if args.dry_run and count == 0:
            sys.exit(EXIT_NO_DATA)
        sys.exit(EXIT_OK)

    elif args.command == "jsonl":
        manifest = export_jsonl(
            sessions_root,
            args.limit,
            args.out,
            args.raw,
            dry_run=args.dry_run,
            session_ids=args.sessions,
            since=args.since,
            filter_text=args.filter,
        )
        count = manifest.get("session_count", 0)
        if args.json:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        elif not getattr(args, "quiet", False):
            if args.dry_run:
                print(f"[dry-run] {count} session(s) found (jsonl); no files written")
            else:
                out_file = manifest.get("jsonl_file")
                print(f"Exported {count} session(s) to: {out_file}")

        if args.dry_run and count == 0:
            sys.exit(EXIT_NO_DATA)
        sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
