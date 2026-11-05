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
