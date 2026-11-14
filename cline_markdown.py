"""Convert Cline CLI 3.x sessions to Markdown with fidelity tracking.

Modes:
    full  -> text, thinking, tool_use, tool_result (each can be disabled)
    clean -> text parts only (default)

Truncation is opt-in per part type via `truncate_thinking` and
`truncate_tools` (0 disables each one). It only applies to `full`; `clean`
has nothing truncatable. `text` and the `tool_use` input are never truncated.
"""

import json
from datetime import datetime

MODES = ("full", "clean")
TRUNCATE_FROM = ("end", "start")

_ROLE_HEADERS = {
    "user": "User",
    "assistant": "Cline",
    "system": "System",
    "tool": "Tool",
}


def _fmt_ts(ts_ms):
    if not isinstance(ts_ms, (int, float)) or ts_ms <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return ""


def _json_block(value):
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _truncate(text, limit, direction, stats, kind):
    if limit <= 0 or len(text) <= limit:
        return text
    stats["truncated_parts"] += 1
    stats[f"truncated_{kind}"] += 1
    removed = len(text) - limit
    marker = f"[... truncated {removed} chars ...]"
    if direction == "start":
        return marker + "\n\n" + text[-limit:].lstrip()
    return text[:limit].rstrip() + "\n\n" + marker


def _tool_result_text(part):
    content = part.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict):
                query = item.get("query")
                result = item.get("result")
                error = item.get("error")
                if query:
                    chunks.append(f"$ {query}")
                if isinstance(result, str) and result:
                    chunks.append(result)
                elif result not in (None, ""):
                    chunks.append(_json_block(result))
                if error:
                    chunks.append(f"[error] {error}")
            elif isinstance(item, str):
                chunks.append(item)
        return "\n\n".join(chunks)
    if content is None:
        return ""
    return _json_block(content)


def _render_part(
    part,
    mode,
    include_thinking,
    include_tool_use,
    include_tool_results,
    truncate_thinking,
    truncate_tools,
    direction,
    stats,
):
    if not isinstance(part, dict):
        return None
    ptype = part.get("type")

    if ptype == "text":
        text = (part.get("text") or "").strip()
        return text or None

    if ptype == "thinking":
        if mode == "clean" or not include_thinking:
            stats["omitted_thinking"] += 1
            return None
        thinking = (part.get("thinking") or "").strip()
        if not thinking:
            return None
        thinking = _truncate(
            thinking, truncate_thinking, direction, stats, "thinking"
        )
        return (
            "<details><summary>Reasoning</summary>\n\n"
            f"{thinking}\n\n</details>"
        )

    if ptype == "tool_use":
        if mode == "clean" or not include_tool_use:
            stats["omitted_tool_use"] += 1
            return None
        name = part.get("name") or "tool"
        body = _json_block(part.get("input"))
        return f"#### Tool call: {name}\n\n```json\n{body}\n```"

    if ptype == "tool_result":
        if mode == "clean" or not include_tool_results:
            stats["omitted_tool_results"] += 1
            return None
        name = part.get("name") or "tool"
        body = _tool_result_text(part).strip()
        body = _truncate(body, truncate_tools, direction, stats, "tools")
        if not body:
            body = "(no output)"
        return f"#### Tool result: {name}\n\n```text\n{body}\n```"

    if not ptype:
        return None

    if mode == "clean":
        stats["omitted_other"] += 1
        return None
    return f"#### {ptype}\n\n```json\n{_json_block(part)}\n```"


def message_to_markdown(
    msg,
    mode,
    include_thinking,
    include_tool_use,
    include_tool_results,
    truncate_thinking,
    truncate_tools,
    direction,
    stats,
):
    role = msg.get("role") or "unknown"
    header = _ROLE_HEADERS.get(role, role)
    content = msg.get("content")
    if isinstance(content, list):
        parts = content
    elif content:
        parts = [content]
    else:
        parts = []

    rendered = []
    for part in parts:
        block = _render_part(
            part,
            mode,
            include_thinking,
            include_tool_use,
            include_tool_results,
            truncate_thinking,
            truncate_tools,
            direction,
            stats,
        )
        if block:
            rendered.append(block)
    if not rendered:
        return None

    lines = [f"## {header}"]
    ts = _fmt_ts(msg.get("ts"))
    if ts:
        lines.append(f"*{ts}*")
    lines.append("")
    lines.append("\n\n".join(rendered))
    lines.append("")
    return "\n".join(lines)


def _yaml(value):
    return json.dumps(value, ensure_ascii=False)


def _bool(value):
    return str(bool(value)).lower()


def _fmt_int(value):
    return f"{value:,}" if isinstance(value, int) else "unknown"


def _fmt_cost(value):
    if isinstance(value, (int, float)):
        return f"${value:.6f}"
    return "unknown"


def _append_usage(lines, usage):
    lines.append("usage:")
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ):
        value = usage.get(key)
        lines.append(f"  {key}: {value if value is not None else 'null'}")
    cost = usage.get("total_cost")
    lines.append(f"  total_cost: {cost if cost is not None else 'null'}")


def _append_models(lines, models):
    lines.append("models:")
    for model in models:
        lines.append(f"  - id: {_yaml(model.get('id') or '')}")
        lines.append(f"    provider: {_yaml(model.get('provider') or '')}")
        family = model.get("family")
        if family:
            lines.append(f"    family: {_yaml(family)}")
        lines.append(f"    messages: {model.get('messages')}")
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            value = model.get(key)
            lines.append(f"    {key}: {value if value is not None else 'null'}")


def session_to_markdown(
    session,
    mode="clean",
    include_thinking=False,
    include_tool_use=False,
    include_tool_results=False,
    truncate_thinking=0,
    truncate_tools=0,
    truncate_from="end",
):
    if mode not in MODES:
        mode = "clean"
    if truncate_from not in TRUNCATE_FROM:
        truncate_from = "end"
    if mode == "clean":
        include_thinking = False
        include_tool_use = False
        include_tool_results = False

    stats = {
        "truncated_parts": 0,
        "truncated_thinking": 0,
        "truncated_tools": 0,
        "omitted_thinking": 0,
        "omitted_tool_use": 0,
        "omitted_tool_results": 0,
        "omitted_other": 0,
    }
    messages = session.messages
    created = session.created_str()

    body = []
    for msg in messages:
        block = message_to_markdown(
            msg,
            mode,
            include_thinking,
            include_tool_use,
            include_tool_results,
            truncate_thinking,
            truncate_tools,
            truncate_from,
            stats,
        )
        if block:
            body.append(block)

    omitted_parts = (
        stats["omitted_thinking"]
        + stats["omitted_tool_use"]
        + stats["omitted_tool_results"]
        + stats["omitted_other"]
    )
    truncated = stats["truncated_parts"] > 0
    faithful = not truncated and omitted_parts == 0

    front = [
        "---",
        f"id: {_yaml(session.id)}",
        f"name: {_yaml(session.name)}",
        f"created: {_yaml(created)}",
        f"messages: {len(messages)}",
        f"export_mode: {mode}",
        f"faithful: {_bool(faithful)}",
        f"include_thinking: {_bool(include_thinking)}",
        f"include_tool_use: {_bool(include_tool_use)}",
        f"include_tool_results: {_bool(include_tool_results)}",
        f"truncated: {_bool(truncated)}",
        f"truncated_parts: {stats['truncated_parts']}",
        f"truncated_thinking: {stats['truncated_thinking']}",
        f"truncated_tools: {stats['truncated_tools']}",
        f"truncate_thinking: {truncate_thinking}",
        f"truncate_tools: {truncate_tools}",
        f"truncate_from: {truncate_from}",
        f"omitted_parts: {omitted_parts}",
        f"omitted_thinking: {stats['omitted_thinking']}",
        f"omitted_tool_use: {stats['omitted_tool_use']}",
        f"omitted_tool_results: {stats['omitted_tool_results']}",
        f"omitted_other: {stats['omitted_other']}",
        f"source_format: {_yaml('cline-cli-3.x')}",
    ]
    if session.provider:
        front.append(f"provider: {_yaml(session.provider)}")
    if session.model:
        front.append(f"model: {_yaml(session.model)}")
    if session.cwd:
        front.append(f"cwd: {_yaml(session.cwd)}")

    extra = session.extra_meta
    front.append(f"source: {_yaml(extra.get('source') or 'cli')}")
    if extra.get("status"):
        front.append(f"status: {_yaml(extra['status'])}")
    if extra.get("ended_at"):
        front.append(f"ended_at: {_yaml(extra['ended_at'])}")

    usage = session.usage
    if usage:
        _append_usage(front, usage)
    models = session.models
    if models:
        _append_models(front, models)

    front.extend(
        [
            "---",
            "",
            f"# {session.name}",
            "",
            f"- **ID:** {session.id}",
            f"- **Created:** {created}",
            f"- **Messages:** {len(messages)}",
            f"- **Mode:** {mode}",
            f"- **Faithful:** {'yes' if faithful else 'no'}",
        ]
    )
    if extra.get("status"):
        status = f"- **Status:** {extra['status']}"
        if extra.get("exit_code") is not None:
            status += f" (exit {extra['exit_code']})"
        front.append(status)
    if extra.get("ended_at"):
        front.append(f"- **Ended:** {extra['ended_at']}")
    if usage:
        front.append(
            "- **Tokens:** "
            f"{_fmt_int(usage.get('input_tokens'))} in · "
            f"{_fmt_int(usage.get('output_tokens'))} out · "
            f"cache read {_fmt_int(usage.get('cache_read_tokens'))} · "
            f"cache write {_fmt_int(usage.get('cache_write_tokens'))}"
        )
        front.append(f"- **Cost:** {_fmt_cost(usage.get('total_cost'))}")
    else:
        front.append("- **Tokens:** unknown")
    if models:
        labels = []
        for model in models:
            label = model.get("id") or "unknown"
            if model.get("messages"):
                label += f" ({model['messages']} msgs)"
            labels.append(label)
        front.append(f"- **Models:** {', '.join(labels)}")
    if truncated:
        front.append(
            f"- **Truncated:** {stats['truncated_parts']} part(s) "
            f"(thinking {stats['truncated_thinking']}, "
            f"tools {stats['truncated_tools']}, from {truncate_from})"
        )
    if omitted_parts:
        front.append(f"- **Omitted parts:** {omitted_parts}")
    front.append("")

    fidelity = {
        "mode": mode,
        "faithful": faithful,
        "include_thinking": include_thinking,
        "include_tool_use": include_tool_use,
        "include_tool_results": include_tool_results,
        "truncated": truncated,
        "truncated_parts": stats["truncated_parts"],
        "truncated_thinking": stats["truncated_thinking"],
        "truncated_tools": stats["truncated_tools"],
        "truncate_thinking": truncate_thinking,
        "truncate_tools": truncate_tools,
        "truncate_from": truncate_from,
        "omitted_parts": omitted_parts,
        "omitted_thinking": stats["omitted_thinking"],
        "omitted_tool_use": stats["omitted_tool_use"],
        "omitted_tool_results": stats["omitted_tool_results"],
        "omitted_other": stats["omitted_other"],
    }
    markdown = "\n".join(front) + "\n" + "\n".join(body) + "\n"
    return markdown, fidelity
