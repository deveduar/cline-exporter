#!/usr/bin/env python3
"""Export Cline CLI 3.x sessions to Markdown, JSONL, memory, and corpus.

Reads `<sessions_root>/<id>/<id>.messages.json` plus `<id>.json` metadata,
including subagent transcripts inside root folders, orders sessions by local
`started_at`, and writes one folder per run.

Usage:
    python cline_exporter.py export
    python cline_exporter.py export --mode full
    python cline_exporter.py export --mode full --no-thinking
    python cline_exporter.py export --mode full --no-tools --truncate 4000
    python cline_exporter.py export --mode full --truncate 4000 --truncate-from start
    python cline_exporter.py export --dry-run
    python cline_exporter.py jsonl
    python cline_exporter.py memory
    python cline_exporter.py corpus
    python cline_exporter.py --check --json
    python cline_exporter.py --list-sessions --json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
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

VERSION = "1.1.0"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NO_DATA = 3

DEFAULT_OUTPUT_DIR = "exports"
JSONL_FILENAME = "all_sessions.jsonl"
RAW_JSONL_FILENAME = "all_sessions_raw.jsonl"
MEMORY_FILENAME = "memory.jsonl"
CORPUS_FILENAME = "corpus.jsonl"
PRODUCTS = ("jsonl", "memory", "corpus")
CORPUS_PARTS = ("text", "thinking", "tool_use", "tool_result", "other")
NAMED_CORPUS_PARTS = CORPUS_PARTS[:-1]
DEFAULT_CORPUS_PARTS = ("text",)

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
        "updated": session.updated_at,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "message_count": len(session.messages),
        "provider": session.provider,
        "model": session.model,
        "cwd": session.cwd,
        "project": session.project,
        "project_id": session.project_id,
        "project_date": getattr(session, "project_date", None),
        "project_folder": getattr(session, "project_folder", None),
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
        "root_session_id": extra.get("root_session_id"),
        "is_root_transcript": extra.get("is_root_transcript"),
        "agent": extra.get("agent"),
        "agent_type": extra.get("agent_type"),
        "parent_thread_id": extra.get("parent_thread_id"),
        "origin_parent_thread_id": extra.get("origin_parent_thread_id"),
        "origin_subagent": extra.get("origin_subagent"),
        "db_parent_session_id": extra.get("db_parent_session_id"),
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


def _date_prefix(session):
    export_dt = session.export_date
    export_utc = export_dt.astimezone(timezone.utc) if export_dt else None
    return export_utc.strftime("%Y-%m-%d") if export_utc else "unknown-date"


def _prepare_project_folders(sessions):
    dates = {}
    for session in sessions:
        export_dt = session.export_date
        export_utc = export_dt.astimezone(timezone.utc) if export_dt else None
        date = export_utc.strftime("%Y-%m-%d") if export_utc else "unknown-date"
        if date != "unknown-date" and (
            session.project_key not in dates or date > dates[session.project_key]
        ):
            dates[session.project_key] = date
    for session in sessions:
        date = dates.get(session.project_key, _date_prefix(session))
        slug = slugify(session.project) or "no-project"
        session.project_date = date
        session.project_folder = f"{date}_{slug}_{session.project_id}"


def _collision_suffixes(sessions, mode):
    groups = {}
    for session in sessions:
        key = (
            session.project_folder,
            slugify(session.name).casefold(),
            mode,
        )
        groups.setdefault(key, []).append(session.id)
    suffixes = {}
    for ids in groups.values():
        for index, sid in enumerate(sorted(ids), 1):
            suffixes[sid] = index
    return suffixes


def _unique_output_file(project_dir, date_prefix, name_slug, mode, suffix=1):
    base = f"{date_prefix}_{name_slug}_{mode}"
    stem = base if suffix <= 1 else f"{base}@{suffix}"
    candidate = project_dir / f"{stem}.md"
    index = suffix + 1
    while candidate.exists():
        candidate = project_dir / f"{base}@{index}.md"
        index += 1
    return candidate


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
    suffix=1,
):
    date_prefix = session.project_date
    name_slug = slugify(session.name)
    project_dir = run_root / session.project_folder
    project_dir.mkdir(parents=True, exist_ok=True)
    out_file = _unique_output_file(
        project_dir, date_prefix, name_slug, mode, suffix=suffix
    )

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
    info["file"] = out_file.relative_to(run_root).as_posix()
    info["absolute_file"] = str(out_file)
    info.update(fidelity)
    return info


def _select_sessions(sessions_root, limit, session_ids, since, filter_text):
    sessions = discover_sessions(sessions_root)
    if session_ids or since or filter_text or limit:
        sessions = filter_sessions(
            sessions,
            last=limit,
            since=since,
            session_ids=session_ids,
            filter_text=filter_text,
        )
    return sessions


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
    sessions = _select_sessions(sessions_root, limit, session_ids, since, filter_text)
    _prepare_project_folders(sessions)

    if dry_run:
        sessions_meta = [_session_metadata(s) for s in sessions]
        totals = _compute_totals(sessions_meta)
        manifest = {
            "tool": "cline_exporter",
            "version": VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "run_folder": None,
            "dry_run": True,
            "writes": False,
            "source": "cli",
            "product": "markdown",
            "project_count": len({s["project_folder"] for s in sessions_meta}),
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
                    "project": s["project"],
                    "status": s["status"],
                }
                for s in sessions_meta
            ],
            "totals": totals,
        }
        return manifest

    base = Path(out_root) if out_root else Path.cwd() / DEFAULT_OUTPUT_DIR
    base.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = _unique_dir(base, f"{run_ts}_{mode}")
    run_root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "tool": "cline_exporter",
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "run_folder": str(run_root),
        "source": "cli",
        "product": "markdown",
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
    suffixes = _collision_suffixes(sessions, mode)
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
                    suffix=suffixes.get(session.id, 1),
                )
            )
        except OSError as exc:
            print(f"Error exporting {session.id}: {exc}", file=sys.stderr)

    manifest["project_count"] = len({s["project_folder"] for s in manifest["sessions"]})
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


def _product_filename(product, raw=False):
    if product == "memory":
        return MEMORY_FILENAME
    if product == "corpus":
        return CORPUS_FILENAME
    return RAW_JSONL_FILENAME if raw else JSONL_FILENAME


def _parse_parts(value, strict=False):
    if value is None:
        return list(DEFAULT_CORPUS_PARTS)
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    parts = []
    invalid = []
    for item in items:
        token = str(item).strip().lower()
        if token == "all":
            return list(CORPUS_PARTS)
        if token in CORPUS_PARTS:
            if token not in parts:
                parts.append(token)
        elif strict:
            invalid.append(token)
    if invalid:
        raise argparse.ArgumentTypeError(
            f"invalid part(s): {', '.join(invalid)}; "
            f"choose from {', '.join(CORPUS_PARTS)} or all"
        )
    return parts or list(DEFAULT_CORPUS_PARTS)


def _parts_arg(value):
    return _parse_parts(value, strict=True)


def build_session_tree(sessions):
    by_id = {s.id: s for s in sessions}
    by_agent = {}
    for session in sessions:
        agent_id = session.extra_meta.get("agent_id")
        if agent_id:
            by_agent.setdefault(str(agent_id), []).append(session)
    candidates = {}
    for session in sorted(sessions, key=lambda s: s.id):
        extra = session.extra_meta
        if not bool(extra.get("is_subagent")):
            continue
        parent = None
        parent_id = extra.get("parent_session_id")
        if parent_id is not None:
            parent = by_id.get(str(parent_id))
            if parent is None:
                for candidate in by_agent.get(str(parent_id), []):
                    if candidate.id != session.id:
                        parent = candidate
                        break
        else:
            agent_id = extra.get("parent_agent_id")
            if agent_id is not None:
                for candidate in by_agent.get(str(agent_id), []):
                    if candidate.id != session.id:
                        parent = candidate
                        break
        if parent is not None and parent.id != session.id:
            candidates[session.id] = parent.id
    parent_of = {}
    for sid in sorted(candidates):
        parent_id = candidates[sid]
        seen = {sid}
        cursor = parent_id
        cyclic = False
        while cursor is not None:
            if cursor in seen:
                cyclic = True
                break
            seen.add(cursor)
            cursor = parent_of.get(cursor)
        if not cyclic:
            parent_of[sid] = parent_id
    children = {s.id: [] for s in sessions}
    for sid, parent_id in parent_of.items():
        children[parent_id].append(by_id[sid])
    roots = [s for s in sessions if s.id not in parent_of]
    reachable = set()
    stack = [s.id for s in roots]
    while stack:
        sid = stack.pop()
        if sid in reachable:
            continue
        reachable.add(sid)
        stack.extend(c.id for c in children.get(sid, []))
    for session in sessions:
        if session.id not in reachable:
            roots.append(session)
    return roots, children


def _memory_payload(session, children, visited=None):
    if visited is None:
        visited = set()
    if session.id in visited:
        return None
    visited.add(session.id)
    payload = _session_metadata(session)
    payload["messages"] = session.messages
    subagents = []
    for child in children.get(session.id, []):
        nested = _memory_payload(child, children, visited)
        if nested is not None:
            subagents.append(nested)
    payload["subagents"] = subagents
    return payload


def _part_text(part):
    ptype = part.get("type") if isinstance(part, dict) else "text"
    if ptype == "text":
        return (part.get("text") or "") if isinstance(part, dict) else str(part)
    if ptype == "thinking":
        return part.get("thinking") or ""
    if ptype in ("tool_use", "tool_result"):
        value = part.get("input") if ptype == "tool_use" else part.get("content")
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return str(value)
    try:
        return json.dumps(part, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(part)


def _flatten_session(session, parts):
    flat = []
    for msg in session.messages:
        content = msg.get("content")
        if isinstance(content, list):
            candidates = content
        elif content is not None:
            candidates = [content]
        else:
            candidates = []
        for part in candidates:
            ptype = part.get("type") if isinstance(part, dict) else "text"
            emitted_type = ptype if ptype in NAMED_CORPUS_PARTS else "other"
            if emitted_type not in parts:
                continue
            text = _part_text(part)
            if not text:
                continue
            flat.append(
                {
                    "session_id": session.id,
                    "role": msg.get("role") or "unknown",
                    "type": emitted_type,
                    "text": text,
                }
            )
    return flat


def _corpus_payload(root, children, parts):
    ordered = []
    visited = set()
    stack = [root]
    while stack:
        current = stack.pop()
        if current.id in visited:
            continue
        visited.add(current.id)
        ordered.append(current)
        stack.extend(reversed(children.get(current.id, [])))
    content = []
    for session in ordered:
        content.extend(_flatten_session(session, parts))
    metadata = _session_metadata(root)
    return {
        "id": root.id,
        "project": root.project,
        "name": root.name,
        "created_at": metadata["created_at"],
        "updated_at": metadata["updated_at"],
        "text": "\n\n".join(item["text"] for item in content),
        "content": content,
        "parts": list(parts),
        "sessions": [
            {
                "id": s.id,
                "project": s.project,
                "agent_id": s.extra_meta.get("agent_id"),
                "parent_session_id": s.extra_meta.get("parent_session_id"),
                "parent_agent_id": s.extra_meta.get("parent_agent_id"),
                "is_subagent": s.extra_meta.get("is_subagent"),
                "message_count": len(s.messages),
            }
            for s in ordered
        ],
        "session_count": len(ordered),
        "message_count": sum(len(s.messages) for s in ordered),
        "metadata": metadata,
    }


def export_products(
    product,
    sessions_root,
    limit,
    out_root,
    raw=False,
    dry_run=False,
    session_ids=None,
    since=None,
    filter_text=None,
    parts=None,
):
    if product not in PRODUCTS:
        raise ValueError(f"Unknown product: {product}")
    sessions = _select_sessions(sessions_root, limit, session_ids, since, filter_text)
    _prepare_project_folders(sessions)
    selected_parts = _parse_parts(parts) if product == "corpus" else []
    roots, children = (
        build_session_tree(sessions) if product in ("memory", "corpus") else ([], {})
    )
    output_count = len(sessions) if product == "jsonl" else len(roots)

    if dry_run:
        manifest = {
            "tool": "cline_exporter",
            "version": VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "run_folder": None,
            "dry_run": True,
            "writes": False,
            "source": "cli",
            "product": product,
            "raw": raw if product == "jsonl" else False,
            "parts": selected_parts if product == "corpus" else None,
            "sessions_root": str(sessions_root),
            "limit": limit,
            "jsonl_file": None,
            "output_file": None,
            "session_count": output_count,
            "selected_session_count": len(sessions),
            "root_session_count": len(roots),
            "project_count": len({s.project_folder for s in sessions}),
        }
        return manifest

    base = Path(out_root) if out_root else Path.cwd() / DEFAULT_OUTPUT_DIR
    base.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = _unique_dir(base, f"{run_ts}_{product}")
    run_root.mkdir(parents=True, exist_ok=True)

    output_file = run_root / _product_filename(product, raw)
    if product == "jsonl" and raw:
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
    elif product == "jsonl":
        lines = [_session_jsonl_line(s) for s in sessions]
    elif product == "memory":
        lines = [
            json.dumps(
                _memory_payload(root, children),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for root in roots
        ]
    else:
        lines = [
            json.dumps(
                _corpus_payload(root, children, selected_parts),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for root in roots
        ]

    count = write_jsonl(output_file, lines)

    manifest = {
        "tool": "cline_exporter",
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "run_folder": str(run_root),
        "source": "cli",
        "product": product,
        "raw": raw if product == "jsonl" else False,
        "parts": selected_parts if product == "corpus" else None,
        "sessions_root": str(sessions_root),
        "limit": limit,
        "jsonl_file": str(output_file),
        "output_file": str(output_file),
        "session_count": count if product == "jsonl" else len(roots),
        "record_count": count,
        "selected_session_count": len(sessions),
        "root_session_count": len(roots),
        "project_count": len({s.project_folder for s in sessions}),
    }
    (run_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


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
    return export_products(
        "jsonl",
        sessions_root,
        limit,
        out_root,
        raw=raw,
        dry_run=dry_run,
        session_ids=session_ids,
        since=since,
        filter_text=filter_text,
    )


def export_memory(
    sessions_root,
    limit,
    out_root,
    dry_run=False,
    session_ids=None,
    since=None,
    filter_text=None,
):
    return export_products(
        "memory",
        sessions_root,
        limit,
        out_root,
        dry_run=dry_run,
        session_ids=session_ids,
        since=since,
        filter_text=filter_text,
    )


def export_corpus(
    sessions_root,
    limit,
    out_root,
    dry_run=False,
    session_ids=None,
    since=None,
    filter_text=None,
    parts=None,
):
    return export_products(
        "corpus",
        sessions_root,
        limit,
        out_root,
        dry_run=dry_run,
        session_ids=session_ids,
        since=since,
        filter_text=filter_text,
        parts=parts,
    )


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
                    "created": s.created_at,
                    "updated": s.updated_at,
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
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
        description="Export Cline CLI sessions to Markdown, JSONL, memory, and corpus",
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
        help="Export sessions to Markdown files grouped by project",
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
        help="Write all sessions to all_sessions.jsonl (legacy product)",
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

    p_memory = sub.add_parser(
        "memory",
        parents=[common],
        help="Write one JSONL record per root session with nested subagents",
    )
    p_memory.add_argument(
        "--out",
        default=None,
        help=f"Output base folder (default: ./{DEFAULT_OUTPUT_DIR})",
    )

    p_corpus = sub.add_parser(
        "corpus",
        parents=[common],
        help="Write one flat corpus document per root session to JSONL",
    )
    p_corpus.add_argument(
        "--out",
        default=None,
        help=f"Output base folder (default: ./{DEFAULT_OUTPUT_DIR})",
    )
    p_corpus.add_argument(
        "--parts",
        type=_parts_arg,
        default=None,
        help=(
            "Comma-separated part types to include "
            f"({','.join(CORPUS_PARTS)}; all; default: {','.join(DEFAULT_CORPUS_PARTS)})"
        ),
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

    elif args.command in PRODUCTS:
        manifest = export_products(
            args.command,
            sessions_root,
            args.limit,
            args.out,
            raw=getattr(args, "raw", False),
            dry_run=args.dry_run,
            session_ids=args.sessions,
            since=args.since,
            filter_text=args.filter,
            parts=getattr(args, "parts", None),
        )
        count = manifest.get("session_count", 0)
        if args.json:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        elif not getattr(args, "quiet", False):
            if args.dry_run:
                print(f"[dry-run] {count} session(s) found ({args.command}); no files written")
            else:
                out_file = manifest.get("jsonl_file")
                print(f"Exported {count} session(s) to: {out_file}")

        if args.dry_run and count == 0:
            sys.exit(EXIT_NO_DATA)
        sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
