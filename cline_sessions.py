"""Discovery and reading of local Cline CLI (3.x) sessions.

Expected layout:
    <sessions_root>/<id>/<id>.json            session metadata
    <sessions_root>/<id>/<id>.messages.json   messages

A root session folder may also hold subagent transcripts named
``<agent>.messages.json`` or ``<agent>__<task>.messages.json``. Their session
IDs are derived as ``<root>__<agent>`` or ``<root>__<agent>__<task>``. The legacy
`ui_messages.json` schema is not supported.
"""

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SESSIONS_ROOT = Path.home() / ".cline" / "data" / "sessions"

_TS_RE = re.compile(r"^(\d{10,})")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PROJECT_RE = re.compile(r'[^a-zA-Z0-9_.-]+')

_USAGE_FIELDS = (
    ("inputTokens", "input_tokens"),
    ("outputTokens", "output_tokens"),
    ("cacheReadTokens", "cache_read_tokens"),
    ("cacheWriteTokens", "cache_write_tokens"),
)
_TOKEN_KEYS = tuple(snake for _, snake in _USAGE_FIELDS)
_MESSAGE_SUFFIX = ".messages.json"
_CHILD_TRANSCRIPT_RE = re.compile(r"^[\w.-]+(?:__[\w.-]+)?$")
_DB_ID_KEYS = ("id", "session_id", "sessionId", "thread_id", "threadId")
_DB_PARENT_KEYS = (
    "parent_id",
    "parent_session_id",
    "parent_thread_id",
    "parentId",
    "parentSessionId",
    "parentThreadId",
)


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


def _iso_utc(value):
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
    return dt.astimezone(timezone.utc)


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


def _coerce_id(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _first_value(data, keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in (None, "", False):
            return value
    return None


def _agent_identifier(value):
    identifier = _coerce_id(value)
    if identifier:
        return identifier
    if isinstance(value, dict):
        for key in ("id", "agentId", "agent_id", "name", "type"):
            identifier = _coerce_id(value.get(key))
            if identifier:
                return identifier
    return None


def _transcript_origin(document):
    origin = document.get("origin") if isinstance(document, dict) else None
    return origin if isinstance(origin, dict) else {}


def _quote_identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def _db_parent_map(sessions_root):
    root = Path(sessions_root)
    candidates = (
        root / "db" / "sessions.db",
        root.parent / "db" / "sessions.db",
    )
    for path in candidates:
        if not path.is_file():
            continue
        parents = {}
        try:
            connection = sqlite3.connect(
                path.resolve().as_uri() + "?mode=ro", uri=True
            )
            try:
                tables = [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                ]
                for table in tables:
                    columns = [
                        row[1]
                        for row in connection.execute(
                            f"PRAGMA table_info({_quote_identifier(table)})"
                        )
                    ]
                    id_column = next(
                        (key for key in _DB_ID_KEYS if key in columns), None
                    )
                    parent_column = next(
                        (key for key in _DB_PARENT_KEYS if key in columns), None
                    )
                    if not id_column or not parent_column:
                        continue
                    query = (
                        f"SELECT {_quote_identifier(id_column)}, "
                        f"{_quote_identifier(parent_column)} "
                        f"FROM {_quote_identifier(table)}"
                    )
                    for sid, parent in connection.execute(query):
                        sid = _coerce_id(sid)
                        parent = _coerce_id(parent)
                        if sid and parent:
                            parents[sid] = parent
            finally:
                connection.close()
        except (sqlite3.Error, OSError, ValueError):
            continue
        if parents:
            return parents
    return {}


def project_key_from_path(value):
    if not isinstance(value, str):
        return ""
    normalized = re.sub(r"/+", "/", value.replace("\\", "/")).rstrip("/")
    if not normalized:
        return ""
    windows_drive = re.match(r"^([A-Za-z]):/(.*)$", normalized)
    wsl_drive = re.match(r"^/mnt/([A-Za-z])/(.*)$", normalized, flags=re.IGNORECASE)
    if windows_drive:
        return f"{windows_drive.group(1)}:/{windows_drive.group(2)}".casefold()
    if wsl_drive:
        return f"{wsl_drive.group(1)}:/{wsl_drive.group(2)}".casefold()
    return normalized.casefold() if os.name == "nt" else normalized


def project_hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def project_from_path(value):
    if not isinstance(value, str):
        return ""
    candidate = value.replace("\\", "/").rstrip("/ \t")
    if not candidate:
        return ""
    candidate = candidate.rsplit("/", 1)[-1].strip()
    if not candidate or candidate in (".", ".."):
        return ""
    candidate = _PROJECT_RE.sub("-", candidate).strip("-._")
    return candidate[:60].strip("-._")


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
    def __init__(self, folder, transcript=None, db_parents=None, session_id=None):
        self.folder = Path(folder)
        self.root_id = self.folder.name
        root_transcript = self.folder / f"{self.root_id}{_MESSAGE_SUFFIX}"
        self.transcript = Path(transcript) if transcript is not None else root_transcript
        self.transcript_stem = (
            self.transcript.name[: -len(_MESSAGE_SUFFIX)] or self.root_id
        )
        self.id = session_id or self.transcript_stem
        self.is_root_transcript = self.transcript == root_transcript
        meta_file = self.transcript.with_name(f"{self.id}.json")
        if not meta_file.exists():
            meta_file = self.transcript.with_name(f"{self.transcript_stem}.json")
        self.meta_file = meta_file
        self.messages_file = self.transcript
        self._db_parents = db_parents or {}
        self._meta = None
        self._document = None
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
        self._project = None
        self._project_key = None

    @property
    def meta(self):
        if self._meta is None:
            data = _load_json(self.meta_file)
            self._meta = data if isinstance(data, dict) else {}
        return self._meta

    @property
    def document(self):
        if self._document is None:
            data = _load_json(self.messages_file)
            self._document = data if isinstance(data, (dict, list)) else {}
        return self._document

    @property
    def messages(self):
        if self._messages is None:
            data = self.document
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
                    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                except (ValueError, OSError, OverflowError):
                    break
        match = _TS_RE.match(self.id)
        if match:
            try:
                return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                pass
        try:
            return datetime.fromtimestamp(self.messages_file.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return None

    @property
    def created(self):
        if self._created is None:
            self._created = self._extract_created()
        return self._created

    @property
    def updated(self):
        candidates = []
        ended = _parse_started_at(self.extra_meta.get("ended_at"))
        if ended:
            candidates.append(ended)
        for source_path in (self.messages_file, self.meta_file):
            try:
                candidates.append(datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc))
            except (OSError, OverflowError, ValueError):
                pass
        for message in self.messages:
            ts = message.get("ts") if isinstance(message, dict) else None
            if isinstance(ts, (int, float)) and ts > 0:
                try:
                    candidates.append(datetime.fromtimestamp(ts / 1000, tz=timezone.utc))
                except (OSError, OverflowError, ValueError):
                    pass
        return max(candidates) if candidates else self.created

    @property
    def created_at(self):
        return _iso_utc(self.created)

    @property
    def updated_at(self):
        return _iso_utc(self.updated)

    def created_str(self, fmt="%Y-%m-%dT%H:%M:%SZ"):
        return self.created.astimezone(timezone.utc).strftime(fmt) if self.created else "unknown"

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
    def project_key(self):
        if self._project_key is None:
            self._project_key = (
                project_key_from_path(self.cwd)
                or project_key_from_path(self.extra_meta.get("workspace_root"))
                or "no-project"
            )
        return self._project_key

    @property
    def project_id(self):
        return project_hash(self.project_key)

    @property
    def project(self):
        if self._project is None:
            self._project = (
                project_from_path(self.cwd)
                or project_from_path(self.extra_meta.get("workspace_root"))
                or "no-project"
            )
        return self._project

    @property
    def export_date(self):
        modified = self.updated
        created = self.created
        if modified and created and modified < created:
            return created
        return modified or created

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
            document = self.document if isinstance(self.document, dict) else {}
            checkpoint = metadata.get("checkpoint")
            latest = (
                checkpoint.get("latest")
                if isinstance(checkpoint, dict)
                else None
            )
            git = metadata.get("git")
            origin = _transcript_origin(document)
            declared_origin = metadata.get("sessionHistoryOrigin")
            if not isinstance(declared_origin, dict):
                declared_origin = document.get("origin")
            parent_thread_id = _coerce_id(
                _first_value(origin, ("parentThreadId", "parent_thread_id"))
            )
            subagent = _first_value(origin, ("subagent", "subAgent"))
            parent_agent_id = _coerce_id(
                _first_value(meta, ("parent_agent_id", "parentAgentId"))
            )
            if parent_agent_id is None:
                parent_agent_id = _coerce_id(
                    _first_value(
                        origin,
                        ("parentAgentId", "parent_agent_id", "parentAgent", "parent_agent"),
                    )
                )
            if parent_agent_id is None and isinstance(subagent, dict):
                parent_agent_id = _coerce_id(
                    _first_value(
                        subagent,
                        ("parentAgentId", "parent_agent_id", "parentAgent", "parent_agent"),
                    )
                )
            db_parent = (
                self._db_parents.get(self.id)
                or self._db_parents.get(self.transcript_stem)
                or self._db_parents.get(self.root_id)
            )
            agent_id = _agent_identifier(subagent)
            if agent_id is None:
                agent_id = _agent_identifier(
                    _first_value(meta, ("agent_id", "agentId"))
                )
            if agent_id is None:
                agent_id = _agent_identifier(document.get("agentId"))
            if agent_id is None and self.is_root_transcript:
                agent_id = _agent_identifier(document.get("agent"))
            is_subagent = meta.get("is_subagent", meta.get("isSubagent"))
            if is_subagent is None:
                is_subagent = bool(
                    not self.is_root_transcript
                    or parent_thread_id
                    or subagent is not None
                    or db_parent
                )
            parent_session_id = parent_thread_id
            if parent_session_id is None:
                parent_session_id = _coerce_id(
                    _first_value(
                        meta,
                        (
                            "parent_session_id",
                            "parentSessionId",
                            "parent_thread_id",
                            "parentThreadId",
                        ),
                    )
                )
            if parent_session_id is None:
                parent_session_id = db_parent
            if parent_session_id is None and is_subagent and not self.is_root_transcript:
                parent_session_id = self.root_id
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
                "parent_session_id": parent_session_id,
                "parent_agent_id": parent_agent_id,
                "agent_id": agent_id,
                "is_subagent": is_subagent,
                "cline_title": metadata.get("title"),
                "git_branch": (
                    git.get("branch") if isinstance(git, dict) else None
                ),
                "checkpoint_latest": (
                    latest if isinstance(latest, dict) else None
                ),
                "session_origin": (
                    declared_origin
                    if isinstance(declared_origin, dict)
                    else (origin or None)
                ),
                "root_session_id": self.root_id,
                "is_root_transcript": self.is_root_transcript,
                "agent": document.get("agent"),
                "agent_type": _coerce_id(
                    _first_value(document, ("agentType", "agent_type"))
                ),
                "parent_thread_id": parent_thread_id,
                "origin_parent_thread_id": parent_thread_id,
                "origin_subagent": (
                    subagent
                    if isinstance(subagent, (dict, str))
                    else None
                ),
                "db_parent_session_id": db_parent,
            }
        return self._extra


def discover_sessions(sessions_root=None, limit=None, include_subagents=True):
    """Discover local sessions, newest first (by started_at)."""
    if sessions_root is None:
        sessions_root = DEFAULT_SESSIONS_ROOT
    root = Path(sessions_root)
    sessions = []
    if root.is_dir():
        db_parents = _db_parent_map(root) if include_subagents else {}
        for folder in sorted(root.iterdir(), key=lambda item: item.name):
            if not folder.is_dir():
                continue
            root_transcript = folder / f"{folder.name}{_MESSAGE_SUFFIX}"
            if not root_transcript.is_file():
                continue
            sessions.append(Session(folder, db_parents=db_parents))
            if not include_subagents:
                continue
            for transcript in sorted(folder.iterdir(), key=lambda item: item.name):
                if transcript == root_transcript or not transcript.is_file():
                    continue
                if not transcript.name.endswith(_MESSAGE_SUFFIX):
                    continue
                stem = transcript.name[: -len(_MESSAGE_SUFFIX)]
                if stem == folder.name or not _CHILD_TRANSCRIPT_RE.fullmatch(stem):
                    continue
                sessions.append(
                    Session(
                        folder,
                        transcript=transcript,
                        db_parents=db_parents,
                        session_id=f"{folder.name}__{stem}",
                    )
                )
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


def count_sessions(sessions_root=None):
    """Count sessions quickly without parsing JSON."""
    if sessions_root is None:
        sessions_root = DEFAULT_SESSIONS_ROOT
    root = Path(sessions_root)
    if not root.is_dir():
        return 0
    count = 0
    for folder in root.iterdir():
        if folder.is_dir() and (folder / f"{folder.name}.messages.json").is_file():
            count += 1
    return count


def filter_sessions(
    sessions,
    last=None,
    since=None,
    session_ids=None,
    filter_text=None,
    order="created-desc",
):
    """Filter and sort a list of Session objects."""
    result = list(sessions)

    if session_ids:
        if isinstance(session_ids, str):
            id_set = {s.strip() for s in session_ids.split(",") if s.strip()}
        else:
            id_set = {str(s).strip() for s in session_ids if str(s).strip()}
        if id_set:
            result = [s for s in result if s.id in id_set]

    if since:
        since_dt = None
        if isinstance(since, str):
            raw = since.strip()
            if len(raw) == 10 and raw.count("-") == 2:
                raw += "T00:00:00"
            try:
                since_dt = datetime.fromisoformat(raw)
            except ValueError:
                since_dt = None
        elif isinstance(since, datetime):
            since_dt = since

        if since_dt:
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            filtered = []
            for s in result:
                if not s.created:
                    continue
                s_dt = s.created
                if s_dt.tzinfo is not None and since_dt.tzinfo is None:
                    since_dt = since_dt.astimezone(s_dt.tzinfo)
                elif s_dt.tzinfo is None and since_dt.tzinfo is not None:
                    s_dt = s_dt.replace(tzinfo=timezone.utc)
                if s_dt >= since_dt:
                    filtered.append(s)
            result = filtered

    if filter_text:
        needle = filter_text.strip().lower()
        result = [
            s
            for s in result
            if (s.name and needle in s.name.lower()) or needle in s.id.lower()
        ]

    # Ordering
    if order == "created-asc":
        result.sort(key=lambda s: s.created.timestamp() if s.created else 0)
    elif order == "created-desc":
        result.sort(
            key=lambda s: s.created.timestamp() if s.created else 0,
            reverse=True,
        )
    elif order == "updated-asc":
        result.sort(
            key=lambda s: s.updated.timestamp() if s.updated else (s.created.timestamp() if s.created else 0)
        )
    elif order == "updated-desc":
        result.sort(
            key=lambda s: s.updated.timestamp() if s.updated else (s.created.timestamp() if s.created else 0),
            reverse=True,
        )

    if last is not None and last > 0:
        result = result[:last]

    return result


def slugify(name, max_len=60):
    name = (name or "session").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "", name)
    name = name.strip("-._") or "session"
    return name[:max_len].strip("-._") or "session"
