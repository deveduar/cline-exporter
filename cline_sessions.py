"""Discovery and reading of local Cline CLI (3.x) sessions.

Expected layout:
    <sessions_root>/<id>/<id>.json            session metadata
    <sessions_root>/<id>/<id>.messages.json   messages

The legacy `ui_messages.json` schema is not supported.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SESSIONS_ROOT = Path.home() / ".cline" / "data" / "sessions"

_TS_RE = re.compile(r"^(\d{10,})")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_USAGE_FIELDS = (
    ("inputTokens", "input_tokens"),
    ("outputTokens", "output_tokens"),
    ("cacheReadTokens", "cache_read_tokens"),
    ("cacheWriteTokens", "cache_write_tokens"),
)
_TOKEN_KEYS = tuple(snake for _, snake in _USAGE_FIELDS)


def derive_title(text, max_len=80):
    """Build a short human-readable title from the first user message."""
    if not text or not isinstance(text, str):
        return ""
    text = re.sub(
        r"<mode_notice>.*?</mode_notice>",
        " ",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = _TAG_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    line = re.sub(r"^[#>\-*\s]+", "", lines[0])
    line = _WS_RE.sub(" ", line).strip()
    sentence = re.search(r"(?<=[.!?])\s+", line)
    if sentence and sentence.start() >= 15:
        line = line[: sentence.start()]
    if len(line) > max_len:
        line = line[:max_len].rstrip() + "..."
    return line


def _parse_started_at(value):
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_usage(raw):
    """Normalize a Cline usage block, keeping unknown values as None."""
    if not isinstance(raw, dict):
        return None
    usage = {key: None for key in _TOKEN_KEYS}
    usage["total_cost"] = None
    found = False
    for raw_key, key in _USAGE_FIELDS:
        value = _as_int(raw.get(raw_key))
        if value is not None:
            usage[key] = value
            found = True
    cost = _as_float(raw.get("totalCost"))
    if cost is not None:
        usage["total_cost"] = cost
        found = True
    return usage if found else None


def _sum_message_metrics(messages):
    """Sum per-message assistant metrics as a fallback for missing usage."""
    usage = {key: 0 for key in _TOKEN_KEYS}
    usage["total_cost"] = None
    found = False
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        metrics = msg.get("metrics")
        if not isinstance(metrics, dict):
            continue
        for raw_key, key in _USAGE_FIELDS:
            value = _as_int(metrics.get(raw_key))
            if value is not None:
                usage[key] += value
                found = True
    return usage if found else None


class Session:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.id = self.folder.name
        self.meta_file = self.folder / f"{self.id}.json"
        self.messages_file = self.folder / f"{self.id}.messages.json"
        self._meta = None
        self._messages = None
        self._name = None
        self._created = None
        self._usage = None
        self._usage_loaded = False
        self._aggregate_usage = None
        self._aggregate_usage_loaded = False
        self._models = None
        self._models_loaded = False
        self._extra = None

    @property
    def meta(self):
        if self._meta is None:
            data = _load_json(self.meta_file)
            self._meta = data if isinstance(data, dict) else {}
        return self._meta

    @property
    def messages(self):
        if self._messages is None:
            data = _load_json(self.messages_file)
            if isinstance(data, dict):
                msgs = data.get("messages")
            elif isinstance(data, list):
                msgs = data
            else:
                msgs = []
            if not isinstance(msgs, list):
                msgs = []
            self._messages = [m for m in msgs if isinstance(m, dict)]
        return self._messages

    def _first_user_text(self):
        for msg in self.messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    title = derive_title(part.get("text"), max_len=80)
                    if title:
                        return title
        return ""

    def _extract_name(self):
        name = self._first_user_text()
        if name:
            return name
        prompt = derive_title(self.meta.get("prompt"), max_len=80)
        if prompt:
            return prompt
        return self.id

    @property
    def name(self):
        if self._name is None:
            self._name = self._extract_name() or self.id
        return self._name

    def _extract_created(self):
        dt = _parse_started_at(self.meta.get("started_at"))
        if dt:
            return dt
        for msg in self.messages:
            ts = msg.get("ts")
            if isinstance(ts, (int, float)) and ts > 0:
                try:
                    return datetime.fromtimestamp(ts / 1000)
                except (ValueError, OSError, OverflowError):
                    break
        match = _TS_RE.match(self.id)
        if match:
            try:
                return datetime.fromtimestamp(int(match.group(1)) / 1000)
            except (ValueError, OSError, OverflowError):
                pass
        try:
            return datetime.fromtimestamp(self.messages_file.stat().st_mtime)
        except OSError:
            return None

    @property
    def created(self):
        if self._created is None:
            self._created = self._extract_created()
        return self._created

    def created_str(self, fmt="%Y-%m-%d %H:%M:%S"):
        return self.created.strftime(fmt) if self.created else "unknown"

    @property
    def provider(self):
        return self.meta.get("provider") or ""

    @property
    def model(self):
        return self.meta.get("model") or ""

    @property
    def cwd(self):
        return self.meta.get("cwd") or ""

    @property
    def metadata(self):
        data = self.meta.get("metadata")
        return data if isinstance(data, dict) else {}

    @property
    def usage(self):
        if not self._usage_loaded:
            self._usage_loaded = True
            usage = _normalize_usage(self.metadata.get("usage"))
            if usage is None:
                usage = _sum_message_metrics(self.messages)
                if usage is not None:
                    cost = _as_float(self.metadata.get("totalCost"))
                    if cost is not None:
                        usage["total_cost"] = cost
            elif usage.get("total_cost") is None:
                cost = _as_float(self.metadata.get("totalCost"))
                if cost is not None:
                    usage["total_cost"] = cost
            self._usage = usage
        return self._usage

    @property
    def aggregate_usage(self):
        if not self._aggregate_usage_loaded:
            self._aggregate_usage_loaded = True
            aggregate = _normalize_usage(self.metadata.get("aggregateUsage"))
            self._aggregate_usage = aggregate if aggregate != self.usage else None
        return self._aggregate_usage

    @property
    def total_cost(self):
        usage = self.usage
        if usage and usage.get("total_cost") is not None:
            return usage["total_cost"]
        return _as_float(self.metadata.get("totalCost"))

    @property
    def aggregated_agents_cost(self):
        return _as_float(self.metadata.get("aggregatedAgentsCost"))

    @property
    def models(self):
        if not self._models_loaded:
            self._models_loaded = True
            buckets = {}
            for msg in self.messages:
                if msg.get("role") != "assistant":
                    continue
                info = msg.get("modelInfo")
                if not isinstance(info, dict):
                    continue
                key = (
                    info.get("id") or "",
                    info.get("provider") or "",
                )
                bucket = buckets.get(key)
                if bucket is None:
                    bucket = {
                        "id": key[0],
                        "provider": key[1],
                        "family": info.get("family") or "",
                        "messages": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "_tokens": False,
                    }
                    buckets[key] = bucket
                elif not bucket["family"] and info.get("family"):
                    bucket["family"] = info["family"]
                bucket["messages"] += 1
                metrics = msg.get("metrics")
                if isinstance(metrics, dict):
                    for raw_key, snake in _USAGE_FIELDS:
                        value = _as_int(metrics.get(raw_key))
                        if value is not None:
                            bucket[snake] += value
                            bucket["_tokens"] = True
            if not buckets:
                self._models = None
            else:
                models = []
                for bucket in sorted(
                    buckets.values(),
                    key=lambda b: (-b["messages"], b["id"]),
                ):
                    if not bucket.pop("_tokens"):
                        for key in _TOKEN_KEYS:
                            bucket[key] = None
                    models.append(bucket)
                self._models = models
        return self._models

    @property
    def extra_meta(self):
        if self._extra is None:
            meta = self.meta
            metadata = self.metadata
            checkpoint = metadata.get("checkpoint")
            latest = (
                checkpoint.get("latest")
                if isinstance(checkpoint, dict)
                else None
            )
            git = metadata.get("git")
            origin = metadata.get("sessionHistoryOrigin")
            self._extra = {
                "status": meta.get("status"),
                "ended_at": meta.get("ended_at"),
                "exit_code": meta.get("exit_code"),
                "interactive": meta.get("interactive"),
                "source": meta.get("source"),
                "workspace_root": meta.get("workspace_root"),
                "team_name": meta.get("team_name"),
                "enable_tools": meta.get("enable_tools"),
                "enable_spawn": meta.get("enable_spawn"),
                "enable_teams": meta.get("enable_teams"),
                "parent_session_id": meta.get("parent_session_id"),
                "parent_agent_id": meta.get("parent_agent_id"),
                "agent_id": meta.get("agent_id"),
                "is_subagent": meta.get("is_subagent"),
                "cline_title": metadata.get("title"),
                "git_branch": (
                    git.get("branch") if isinstance(git, dict) else None
                ),
                "checkpoint_latest": (
                    latest if isinstance(latest, dict) else None
                ),
                "session_origin": (
                    origin if isinstance(origin, dict) else None
                ),
            }
        return self._extra


def discover_sessions(sessions_root=None, limit=None):
    """Discover local sessions, newest first (by started_at)."""
    if sessions_root is None:
        sessions_root = DEFAULT_SESSIONS_ROOT
    root = Path(sessions_root)
    sessions = []
    if root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            if (folder / f"{folder.name}.messages.json").is_file():
                sessions.append(Session(folder))
    sessions.sort(
        key=lambda s: s.created.timestamp() if s.created else 0,
        reverse=True,
    )
    if limit:
        sessions = sessions[:limit]
    return sessions


def resolve_data_dir_sessions(data_dir):
    """Resolve the sessions folder inside a Cline --data-dir."""
    base = Path(data_dir)
    for sub in (base / "data" / "sessions", base / "sessions", base):
        if sub.is_dir() and any(
            (d / f"{d.name}.messages.json").is_file()
            for d in sub.iterdir()
            if d.is_dir()
        ):
            return sub
    return base / "data" / "sessions"


def slugify(name, max_len=60):
    name = (name or "session").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "", name)
    name = name.strip("-._") or "session"
    return name[:max_len].strip("-._") or "session"
