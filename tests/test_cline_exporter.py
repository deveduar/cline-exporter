import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cline_exporter import (
    _session_metadata,
    export_all,
    export_corpus,
    export_jsonl,
    export_memory,
    export_products,
)
from cline_sessions import (
    Session,
    discover_sessions,
    project_from_path,
    project_hash,
    project_key_from_path,
)

SCRIPT = Path(__file__).resolve().parents[1] / "cline_exporter.py"


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *[str(a) for a in args]],
        capture_output=True,
        text=True,
    )


def text_msg(role, text, ts=None):
    msg = {"role": role, "content": [{"type": "text", "text": text}]}
    if ts is not None:
        msg["ts"] = ts
    return msg


def thinking_msg(role, thinking):
    return {"role": role, "content": [{"type": "thinking", "thinking": thinking}]}


def make_session(
    root,
    sid,
    cwd=None,
    workspace_root=None,
    started_at="2026-01-02T03:04:05Z",
    messages=None,
    agent_id=None,
    parent_session_id=None,
    parent_agent_id=None,
    is_subagent=False,
    mtime=None,
):
    folder = root / sid
    folder.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": sid,
        "started_at": started_at,
        "cwd": cwd,
        "workspace_root": workspace_root,
        "provider": "cline",
        "model": "test-model",
        "agent_id": agent_id,
        "parent_session_id": parent_session_id,
        "parent_agent_id": parent_agent_id,
        "is_subagent": is_subagent,
    }
    meta_file = folder / f"{sid}.json"
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    messages_file = folder / f"{sid}.messages.json"
    messages_file.write_text(
        json.dumps({"messages": messages or []}), encoding="utf-8"
    )
    if mtime is not None:
        stamp = datetime.fromisoformat(mtime.replace("Z", "+00:00")).timestamp()
        os.utime(messages_file, (stamp, stamp))
        os.utime(meta_file, (stamp, stamp))
    return Session(folder)


def make_subagent_transcript(folder, name, messages=None, role=None, origin=None):
    target = folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {"messages": messages or []}
    if role is not None:
        document["agent"] = role
    if origin is not None:
        document["origin"] = origin
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def nested_ids(records):
    found = []

    def walk(record):
        found.append(record["id"])
        for child in record.get("subagents", []):
            walk(child)

    for record in records:
        walk(record)
    return found


class ProjectDerivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_project_from_posix_cwd(self):
        session = make_session(self.root, "posix", cwd="/home/alice/my-project")
        self.assertEqual(session.project, "my-project")

    def test_project_from_windows_cwd(self):
        session = make_session(self.root, "windows", cwd=r"C:\Users\alice\My Project")
        self.assertEqual(session.project, "My-Project")
        trailing = make_session(self.root, "windows-slash", cwd=r"C:\work\alpha\\")
        self.assertEqual(trailing.project, "alpha")

    def test_project_from_workspace_fallback(self):
        session = make_session(
            self.root,
            "workspace",
            cwd="",
            workspace_root=r"D:\work\team-space",
        )
        self.assertEqual(session.project, "team-space")

    def test_project_missing_fallback(self):
        session = make_session(self.root, "missing")
        self.assertEqual(session.project, "no-project")

    def test_project_sanitized(self):
        session = make_session(self.root, "sanitize", cwd="/work/bad<name>:?")
        self.assertEqual(session.project, "bad-name")
        self.assertEqual(project_from_path("///"), "")

    def test_project_id_is_stable_across_windows_and_wsl_paths(self):
        windows = make_session(self.root, "windows-id", cwd=r"C:\work\My Project")
        wsl = make_session(self.root, "wsl-id", cwd="/mnt/c/work/My Project/")
        self.assertEqual(windows.project_key, wsl.project_key)
        self.assertEqual(windows.project_id, wsl.project_id)
        self.assertEqual(len(windows.project_id), 12)
        self.assertRegex(windows.project_id, r"^[0-9a-f]{12}$")

    def test_project_id_missing_fallback(self):
        session = make_session(self.root, "missing-id")
        self.assertEqual(session.project_key, "no-project")
        self.assertEqual(session.project_id, project_hash("no-project"))


class MarkdownLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions"
        self.root.mkdir()
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def export(self):
        return export_all(
            self.root,
            None,
            self.out,
            "conversation",
            False,
            False,
            False,
            0,
            0,
            "end",
        )

    def test_files_written_directly_inside_project_folder(self):
        make_session(
            self.root,
            "one",
            cwd="/work/proj-one",
            messages=[text_msg("user", "hello one")],
            mtime="2026-03-04T00:00:00Z",
        )
        make_session(
            self.root,
            "two",
            cwd="/work/proj-two",
            messages=[text_msg("user", "hello two")],
            mtime="2026-03-04T00:00:00Z",
        )
        manifest = self.export()
        run_dir = Path(manifest["run_folder"])
        self.assertTrue(run_dir.name.endswith("_conversation"))
        self.assertEqual(manifest["project_count"], 2)
        entries = {entry["id"]: entry for entry in manifest["sessions"]}
        expected = {
            "one": ("/work/proj-one", "2026-03-04_hello-one_conversation.md"),
            "two": ("/work/proj-two", "2026-03-04_hello-two_conversation.md"),
        }
        folders = []
        for sid, (cwd, filename) in expected.items():
            slug = project_from_path(cwd)
            project_id = project_hash(project_key_from_path(cwd))
            folder = f"2026-03-04_{slug}_{project_id}"
            folders.append(folder)
            rel = f"{folder}/{filename}"
            self.assertEqual(entries[sid]["file"], rel)
            self.assertEqual(entries[sid]["project"], slug)
            self.assertEqual(entries[sid]["project_id"], project_id)
            self.assertEqual(entries[sid]["project_date"], "2026-03-04")
            self.assertEqual(entries[sid]["project_folder"], folder)
            self.assertTrue((run_dir / rel).is_file())
            self.assertEqual(Path(entries[sid]["absolute_file"]), run_dir / rel)
        top_dirs = [p.name for p in run_dir.iterdir() if p.is_dir()]
        self.assertEqual(sorted(top_dirs), sorted(folders))

    def test_date_prefix_uses_last_modified_then_created(self):
        make_session(
            self.root,
            "modified",
            cwd="/work/dates",
            started_at="2020-01-01T00:00:00Z",
            messages=[text_msg("user", "dated task")],
            mtime="2026-07-08T00:00:00Z",
        )
        manifest = self.export()
        rel = manifest["sessions"][0]["file"]
        self.assertTrue(Path(rel).name.startswith("2026-07-08_dated-task_"))

        stale = make_session(
            self.root,
            "created",
            cwd="/work/dates",
            started_at="2020-02-03T00:00:00Z",
            messages=[text_msg("user", "created task")],
        )
        os.utime(stale.messages_file, (0, 0))
        os.utime(stale.meta_file, (0, 0))
        manifest = self.export()
        names = sorted(Path(entry["file"]).name for entry in manifest["sessions"])
        self.assertEqual(
            names,
            [
                "2020-02-03_created-task_conversation.md",
                "2026-07-08_dated-task_conversation.md",
            ],
        )
        folders = {
            Path(entry["file"]).parts[0]
            for entry in manifest["sessions"]
        }
        self.assertEqual(
            folders,
            {f"2026-07-08_dates_{project_hash(project_key_from_path('/work/dates'))}"},
        )

    def test_collision_adds_deterministic_suffix(self):
        for sid in ("first", "second"):
            make_session(
                self.root,
                sid,
                cwd="/work/shared",
                messages=[text_msg("user", "same title")],
                mtime="2026-03-04T00:00:00Z",
            )
        manifest = self.export()
        names = sorted(Path(entry["file"]).name for entry in manifest["sessions"])
        self.assertEqual(
            names,
            [
                "2026-03-04_same-title_conversation.md",
                "2026-03-04_same-title_conversation@2.md",
            ],
        )

    def test_collision_suffix_ignores_encounter_order(self):
        make_session(
            self.root,
            "z-new",
            cwd="/work/shared",
            started_at="2026-02-01T00:00:00Z",
            messages=[text_msg("user", "same title")],
            mtime="2026-03-04T00:00:00Z",
        )
        make_session(
            self.root,
            "a-old",
            cwd="/work/shared",
            started_at="2026-01-01T00:00:00Z",
            messages=[text_msg("user", "same title")],
            mtime="2026-03-04T00:00:00Z",
        )
        manifest = self.export()
        files = {
            entry["id"]: Path(entry["file"]).name for entry in manifest["sessions"]
        }
        self.assertEqual(files["a-old"], "2026-03-04_same-title_conversation.md")
        self.assertEqual(files["z-new"], "2026-03-04_same-title_conversation@2.md")

    def test_collision_grouping_ignores_case(self):
        make_session(
            self.root,
            "upper",
            cwd="/work/shared",
            messages=[text_msg("user", "Same Title")],
            mtime="2026-03-04T00:00:00Z",
        )
        make_session(
            self.root,
            "lower",
            cwd="/work/shared",
            messages=[text_msg("user", "same title")],
            mtime="2026-03-04T00:00:00Z",
        )
        manifest = self.export()
        files = {
            entry["id"]: Path(entry["file"]).name for entry in manifest["sessions"]
        }
        self.assertEqual(
            files["lower"], "2026-03-04_same-title_conversation.md"
        )
        self.assertEqual(
            files["upper"], "2026-03-04_Same-Title_conversation@2.md"
        )


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions"
        self.root.mkdir()
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def build_tree(self):
        root = make_session(
            self.root,
            "root-session",
            cwd="/work/memory",
            agent_id="root-agent",
            messages=[text_msg("user", "root message")],
        )
        child = make_session(
            self.root,
            "child-session",
            cwd="/work/memory",
            agent_id="child-agent",
            parent_session_id="root-session",
            parent_agent_id="root-agent",
            is_subagent=True,
            messages=[text_msg("assistant", "child message")],
        )
        make_session(
            self.root,
            "agent-child",
            cwd="/work/memory",
            parent_agent_id="root-agent",
            is_subagent=True,
            messages=[text_msg("assistant", "agent child message")],
        )
        make_session(
            self.root,
            "grandchild-session",
            cwd="/work/memory",
            parent_session_id="child-session",
            parent_agent_id="child-agent",
            is_subagent=True,
            messages=[text_msg("assistant", "grandchild message")],
        )
        return root, child

    def test_memory_nests_subagents_and_keeps_raw_messages(self):
        root, child = self.build_tree()
        manifest = export_memory(self.root, None, self.out)
        run_dir = Path(manifest["run_folder"])
        self.assertTrue(run_dir.name.endswith("_memory"))
        self.assertEqual(Path(manifest["jsonl_file"]).name, "memory.jsonl")
        self.assertEqual(manifest["product"], "memory")
        self.assertEqual(manifest["session_count"], 1)
        self.assertEqual(manifest["selected_session_count"], 4)
        self.assertEqual(manifest["root_session_count"], 1)
        records = read_jsonl(manifest["jsonl_file"])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["id"], "root-session")
        self.assertEqual(record["messages"], root.messages)
        self.assertEqual(record["project"], "memory")
        self.assertEqual(len(record["subagents"]), 2)
        by_id = {entry["id"]: entry for entry in record["subagents"]}
        self.assertEqual(set(by_id), {"child-session", "agent-child"})
        nested = by_id["child-session"]["subagents"]
        self.assertEqual(len(nested), 1)
        self.assertEqual(nested[0]["id"], "grandchild-session")
        self.assertEqual(nested[0]["messages"][0]["content"][0]["text"], "grandchild message")
        self.assertNotIn("child-session", [r["id"] for r in records])
        self.assertEqual(child.messages, by_id["child-session"]["messages"])

    def test_corpus_flattens_text_and_includes_children(self):
        root, _ = self.build_tree()
        session = make_session(
            self.root,
            "thought-session",
            cwd="/work/corpus",
            parent_session_id=root.id,
            is_subagent=True,
            messages=[
                text_msg("user", "visible text"),
                thinking_msg("assistant", "hidden thought"),
            ],
        )
        manifest = export_corpus(self.root, None, self.out, parts=("text",))
        run_dir = Path(manifest["run_folder"])
        self.assertTrue(run_dir.name.endswith("_corpus"))
        self.assertEqual(Path(manifest["jsonl_file"]).name, "corpus.jsonl")
        docs = read_jsonl(manifest["jsonl_file"])
        self.assertEqual(len(docs), 1)
        own = docs[0]
        self.assertEqual(own["id"], "root-session")
        self.assertIn("root message", own["text"])
        self.assertIn("child message", own["text"])
        self.assertIn("agent child message", own["text"])
        self.assertIn("grandchild message", own["text"])
        self.assertIn("visible text", own["text"])
        self.assertNotIn("hidden thought", own["text"])
        self.assertEqual(own["session_count"], 5)
        self.assertEqual(own["project"], "memory")
        self.assertEqual(own["parts"], ["text"])
        self.assertTrue(all(item["type"] == "text" for item in own["content"]))
        self.assertTrue(all(item["text"] for item in own["content"]))
        self.assertEqual(
            {entry["id"] for entry in own["sessions"]},
            {
                "root-session",
                "child-session",
                "agent-child",
                "grandchild-session",
                "thought-session",
            },
        )

        with_thinking = export_corpus(
            self.root,
            None,
            self.out,
            parts=("text", "thinking"),
            session_ids=session.id,
        )
        doc = read_jsonl(with_thinking["jsonl_file"])[0]
        self.assertIn("visible text", doc["text"])
        self.assertIn("hidden thought", doc["text"])

    def test_corpus_other_parts_keep_unknown_types(self):
        make_session(
            self.root,
            "images",
            cwd="/work/parts",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "visible text"},
                        {"type": "image", "mime": "image/png", "data": "abc123"},
                    ],
                }
            ],
        )
        other = export_corpus(self.root, None, self.out, parts=("other",))
        doc = read_jsonl(other["jsonl_file"])[0]
        self.assertEqual([item["type"] for item in doc["content"]], ["other"])
        self.assertIn("abc123", doc["text"])
        self.assertIn("image/png", doc["text"])
        self.assertNotIn("visible text", doc["text"])
        self.assertEqual(doc["parts"], ["other"])

        everything = export_corpus(self.root, None, self.out, parts=("all",))
        doc = read_jsonl(everything["jsonl_file"])[0]
        self.assertEqual(
            [item["type"] for item in doc["content"]], ["text", "other"]
        )
        self.assertIn("visible text", doc["text"])
        self.assertIn("abc123", doc["text"])
        self.assertIn("other", doc["parts"])

        text_only = export_corpus(self.root, None, self.out, parts=("text",))
        doc = read_jsonl(text_only["jsonl_file"])[0]
        self.assertEqual([item["type"] for item in doc["content"]], ["text"])
        self.assertNotIn("abc123", doc["text"])

    def test_legacy_jsonl_unchanged(self):
        make_session(
            self.root,
            "legacy",
            cwd="/work/legacy",
            messages=[text_msg("user", "legacy text")],
        )
        manifest = export_jsonl(self.root, None, self.out)
        run_dir = Path(manifest["run_folder"])
        self.assertTrue(run_dir.name.endswith("_jsonl"))
        self.assertEqual(Path(manifest["jsonl_file"]).name, "all_sessions.jsonl")
        self.assertEqual(manifest["session_count"], 1)
        records = read_jsonl(manifest["jsonl_file"])
        self.assertEqual(records[0]["messages"][0]["content"][0]["text"], "legacy text")
        self.assertEqual(records[0]["project"], "legacy")

        raw_manifest = export_jsonl(self.root, None, self.out, raw=True)
        self.assertEqual(
            Path(raw_manifest["jsonl_file"]).name, "all_sessions_raw.jsonl"
        )
        self.assertIn("messages", read_jsonl(raw_manifest["jsonl_file"])[0])

    def test_dry_run_writes_nothing(self):
        make_session(
            self.root,
            "dry",
            cwd="/work/dry",
            messages=[text_msg("user", "dry text")],
        )
        before = sorted(p.name for p in self.out.iterdir()) if self.out.exists() else []
        for product in ("jsonl", "memory", "corpus"):
            manifest = export_products(product, self.root, None, self.out, dry_run=True)
            self.assertTrue(manifest["dry_run"])
            self.assertFalse(manifest["writes"])
            self.assertIsNone(manifest["run_folder"])
            self.assertIsNone(manifest["jsonl_file"])
            self.assertIsNone(manifest["output_file"])
            self.assertEqual(manifest["product"], product)
        after = sorted(p.name for p in self.out.iterdir()) if self.out.exists() else []
        self.assertEqual(before, after)

    def test_json_envelope_fields(self):
        session = make_session(
            self.root,
            "envelope",
            cwd=r"C:\work\envelope",
            messages=[text_msg("user", "envelope text")],
        )
        metadata = _session_metadata(session)
        for key in (
            "id",
            "project",
            "project_id",
            "project_date",
            "project_folder",
            "cwd",
            "source_root",
            "source_format",
            "parent_session_id",
            "parent_agent_id",
            "agent_id",
            "is_subagent",
            "revision",
            "usage",
            "models",
        ):
            self.assertIn(key, metadata)
        self.assertEqual(metadata["project"], "envelope")
        self.assertEqual(
            metadata["project_id"],
            project_hash(project_key_from_path(r"C:\work\envelope")),
        )

        manifest = export_all(
            self.root,
            None,
            self.out,
            "conversation",
            False,
            False,
            False,
            0,
            0,
            "end",
        )
        entry = manifest["sessions"][0]
        self.assertRegex(
            entry["file"],
            rf"^\d{{4}}-\d{{2}}-\d{{2}}_envelope_{metadata['project_id']}/",
        )
        self.assertNotIn(":", Path(entry["file"]).parts[0])
        self.assertTrue((Path(manifest["run_folder"]) / entry["file"]).is_file())


class SubagentDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.root = self.data / "sessions"
        self.root.mkdir(parents=True)
        self.db_path = self.data / "db" / "sessions.db"
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def test_named_and_task_transcripts_are_nested_under_root(self):
        folder = make_session(
            self.root,
            "root-1",
            cwd="/work/agents",
            agent_id="root-agent",
            messages=[text_msg("user", "root request")],
        )
        make_subagent_transcript(
            folder.folder,
            "explore.messages.json",
            messages=[text_msg("assistant", "explore output")],
            role="subagent",
            origin={"parentThreadId": "root-1", "subagent": "agent-explore-1"},
        )
        make_subagent_transcript(
            folder.folder,
            "explore__task.messages.json",
            messages=[text_msg("assistant", "task output")],
            role="teammate",
            origin={"parentThreadId": "root-1", "subagent": "agent-task-1"},
        )
        make_subagent_transcript(
            folder.folder,
            "subagents/ignored.messages.json",
            messages=[text_msg("assistant", "nested output")],
            role="subagent",
            origin={"parentThreadId": "root-1", "subagent": "agent-ignored"},
        )

        sessions = discover_sessions(self.root)
        self.assertEqual(
            {s.id for s in sessions},
            {"root-1", "root-1__explore", "root-1__explore__task"},
        )
        by_id = {s.id: s for s in sessions}
        explore = by_id["root-1__explore"].extra_meta
        self.assertTrue(explore["is_subagent"])
        self.assertEqual(explore["parent_session_id"], "root-1")
        self.assertEqual(explore["parent_thread_id"], "root-1")
        self.assertEqual(explore["agent_id"], "agent-explore-1")
        self.assertEqual(explore["agent"], "subagent")
        self.assertEqual(explore["origin_subagent"], "agent-explore-1")
        self.assertEqual(explore["root_session_id"], "root-1")
        task = by_id["root-1__explore__task"].extra_meta
        self.assertEqual(task["agent_id"], "agent-task-1")
        self.assertEqual(task["agent"], "teammate")
        self.assertEqual(task["parent_session_id"], "root-1")

        manifest = export_memory(self.root, None, self.out)
        self.assertEqual(manifest["session_count"], 1)
        self.assertEqual(manifest["selected_session_count"], 3)
        records = read_jsonl(manifest["jsonl_file"])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "root-1")
        self.assertEqual(
            {entry["id"] for entry in records[0]["subagents"]},
            {"root-1__explore", "root-1__explore__task"},
        )

        corpus = export_corpus(self.root, None, self.out, parts=("text",))
        docs = read_jsonl(corpus["jsonl_file"])
        self.assertEqual(len(docs), 1)
        self.assertIn("root request", docs[0]["text"])
        self.assertIn("explore output", docs[0]["text"])
        self.assertIn("task output", docs[0]["text"])
        self.assertNotIn("nested output", docs[0]["text"])
        self.assertEqual(docs[0]["session_count"], 3)

        raw = export_jsonl(self.root, None, self.out, raw=True)
        self.assertEqual(len(read_jsonl(raw["jsonl_file"])), 4)

    def test_db_parent_linkage_is_used(self):
        make_session(
            self.root,
            "other-root",
            cwd="/work/db",
            messages=[text_msg("user", "other request")],
        )
        folder = make_session(
            self.root,
            "source-root",
            cwd="/work/db",
            messages=[text_msg("user", "source request")],
        )
        make_subagent_transcript(
            folder.folder,
            "explore.messages.json",
            messages=[text_msg("assistant", "db child")],
            role="subagent",
            origin={"subagent": "agent-db-child"},
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.execute("CREATE TABLE sessions (id TEXT, parent_id TEXT)")
        connection.execute(
            "INSERT INTO sessions (id, parent_id) VALUES (?, ?)",
            ("source-root__explore", "other-root"),
        )
        connection.commit()
        connection.close()

        by_id = {s.id: s for s in discover_sessions(self.root)}
        child = by_id["source-root__explore"].extra_meta
        self.assertEqual(child["db_parent_session_id"], "other-root")
        self.assertEqual(child["agent_id"], "agent-db-child")
        self.assertEqual(child["parent_session_id"], "other-root")
        records = read_jsonl(export_memory(self.root, None, self.out)["jsonl_file"])
        self.assertEqual({r["id"] for r in records}, {"other-root", "source-root"})
        other = next(r for r in records if r["id"] == "other-root")
        self.assertEqual([c["id"] for c in other["subagents"]], ["source-root__explore"])

    def test_self_referencing_origin_parent_does_not_loop(self):
        folder = make_session(
            self.root,
            "cycle-root",
            cwd="/work/edges",
            messages=[text_msg("user", "cycle request")],
        )
        make_subagent_transcript(
            folder.folder,
            "loop.messages.json",
            messages=[text_msg("assistant", "loop output")],
            role="subagent",
            origin={
                "parentThreadId": "cycle-root__loop",
                "subagent": "agent-loop",
            },
        )
        records = read_jsonl(export_memory(self.root, None, self.out)["jsonl_file"])
        self.assertEqual(
            sorted(nested_ids(records)), ["cycle-root", "cycle-root__loop"]
        )
        corpus = export_corpus(self.root, None, self.out, parts=("text",))
        self.assertEqual(len(read_jsonl(corpus["jsonl_file"])), 2)


class TreeEdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions"
        self.root.mkdir()
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def memory_records(self):
        manifest = export_memory(self.root, None, self.out)
        return read_jsonl(manifest["jsonl_file"]), manifest

    def test_unresolved_session_id_does_not_fall_back_to_agent_id(self):
        make_session(
            self.root,
            "sibling",
            cwd="/work/edges",
            agent_id="shared-agent",
            messages=[text_msg("user", "sibling text")],
        )
        make_session(
            self.root,
            "orphan",
            cwd="/work/edges",
            is_subagent=True,
            parent_session_id="missing-id",
            parent_agent_id="shared-agent",
            messages=[text_msg("user", "orphan text")],
        )
        records, manifest = self.memory_records()
        self.assertEqual(manifest["session_count"], 2)
        self.assertEqual({r["id"] for r in records}, {"sibling", "orphan"})
        by_id = {r["id"]: r for r in records}
        self.assertEqual(by_id["sibling"]["subagents"], [])
        self.assertEqual(by_id["orphan"]["subagents"], [])

    def test_unmarked_session_with_parent_fields_stays_root(self):
        make_session(
            self.root,
            "parent",
            cwd="/work/edges",
            agent_id="parent-agent",
            messages=[text_msg("user", "parent text")],
        )
        make_session(
            self.root,
            "unmarked",
            cwd="/work/edges",
            is_subagent=False,
            parent_session_id="parent",
            parent_agent_id="parent-agent",
            messages=[text_msg("user", "unmarked text")],
        )
        records, _ = self.memory_records()
        self.assertEqual({r["id"] for r in records}, {"parent", "unmarked"})
        self.assertTrue(all(r["subagents"] == [] for r in records))

    def test_cyclic_parent_metadata_terminates(self):
        make_session(
            self.root,
            "cycle-a",
            cwd="/work/edges",
            agent_id="agent-a",
            is_subagent=True,
            parent_session_id="cycle-b",
            parent_agent_id="agent-b",
            messages=[text_msg("user", "cycle a text")],
        )
        make_session(
            self.root,
            "cycle-b",
            cwd="/work/edges",
            agent_id="agent-b",
            is_subagent=True,
            parent_session_id="cycle-a",
            parent_agent_id="agent-a",
            messages=[text_msg("user", "cycle b text")],
        )
        records, _ = self.memory_records()
        self.assertEqual(sorted(nested_ids(records)), ["cycle-a", "cycle-b"])

        corpus = export_corpus(self.root, None, self.out, parts=("text",))
        docs = read_jsonl(corpus["jsonl_file"])
        self.assertEqual(sorted(d["id"] for d in docs), ["cycle-b"])
        session_ids = [item["id"] for item in docs[0]["sessions"]]
        self.assertEqual(sorted(session_ids), ["cycle-a", "cycle-b"])


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions"
        self.root.mkdir()
        self.out = Path(self.tmp.name) / "out"
        make_session(
            self.root,
            "cli-root",
            cwd=r"C:\work\cli",
            agent_id="cli-agent",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "cli root text"},
                        {"type": "image", "data": "cli-image-data"},
                    ],
                },
                thinking_msg("assistant", "cli hidden thought"),
            ],
        )
        make_session(
            self.root,
            "cli-child",
            cwd=r"C:\work\cli",
            is_subagent=True,
            parent_session_id="cli-root",
            messages=[text_msg("assistant", "cli child text")],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, command, *args, out=None):
        target = self.out / (out or command)
        result = run_cli(
            command,
            "--sessions-root",
            self.root,
            "--out",
            target,
            *args,
        )
        return result, target

    def test_cli_memory_json(self):
        result, out = self.cli("memory", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        self.assertEqual(manifest["product"], "memory")
        self.assertEqual(manifest["session_count"], 1)
        self.assertEqual(manifest["selected_session_count"], 2)
        self.assertEqual(Path(manifest["jsonl_file"]).name, "memory.jsonl")
        self.assertTrue(Path(manifest["run_folder"]).name.endswith("_memory"))
        records = read_jsonl(manifest["jsonl_file"])
        self.assertEqual(records[0]["id"], "cli-root")
        self.assertEqual(records[0]["subagents"][0]["id"], "cli-child")
        self.assertTrue(out.exists())

    def test_cli_run_label_controls_run_folder(self):
        result, out = self.cli("memory", "--json", "--run-label", "broad")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        self.assertEqual(manifest["run_label"], "broad")
        self.assertTrue(Path(manifest["run_folder"]).name.endswith("_broad"))
        self.assertTrue(out.exists())

    def test_cli_corpus_parts_and_invalid_value(self):
        result, _ = self.cli("corpus", "--parts", "text,thinking", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        self.assertEqual(manifest["product"], "corpus")
        self.assertEqual(manifest["parts"], ["text", "thinking"])
        doc = read_jsonl(manifest["jsonl_file"])[0]
        self.assertIn("cli root text", doc["text"])
        self.assertIn("cli child text", doc["text"])
        self.assertIn("cli hidden thought", doc["text"])

        invalid, _ = self.cli("corpus", "--parts", "bogus", out="invalid")
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("invalid part", invalid.stderr)

    def test_cli_corpus_other_and_all_include_unknown_types(self):
        for parts in ("other", "all"):
            with self.subTest(parts=parts):
                result, out = self.cli(
                    "corpus", "--parts", parts, "--json", out=f"parts-{parts}"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                manifest = json.loads(result.stdout)
                doc = read_jsonl(manifest["jsonl_file"])[0]
                self.assertIn("other", doc["parts"])
                other_items = [
                    item for item in doc["content"] if item["type"] == "other"
                ]
                self.assertEqual(len(other_items), 1)
                self.assertIn("cli-image-data", other_items[0]["text"])
                if parts == "other":
                    self.assertNotIn("cli root text", doc["text"])
                else:
                    self.assertIn("cli root text", doc["text"])

    def test_cli_dry_run_json(self):
        for command, product in (
            ("export", "markdown"),
            ("jsonl", "jsonl"),
            ("memory", "memory"),
            ("corpus", "corpus"),
        ):
            with self.subTest(command=command):
                result, out = self.cli(command, "--dry-run", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                manifest = json.loads(result.stdout)
                self.assertEqual(manifest["product"], product)
                self.assertTrue(manifest["dry_run"])
                self.assertFalse(manifest["writes"])
                self.assertIsNone(manifest["run_folder"])
                self.assertIsNone(manifest.get("jsonl_file"))
                self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
