"""Durable project and asset storage for the local music workbench.

The database stores identities and immutable revisions. Large payloads live in a
content-addressed blob directory so the same bytes can be referenced by several
logical assets without conflating their title, provenance or rights metadata.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import sqlite3
import time
import uuid
import wave
from contextlib import contextmanager
from pathlib import Path

from .io import sha256, within

IDENTIFIER = re.compile(r"[a-f0-9]{32}")
AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac"}
ASSET_KINDS = {
    "song", "reference_voice", "vocal", "instrumental", "lyrics", "style",
    "score", "work", "model", "other",
}
TEXT_KINDS = {"lyrics", "style", "score"}


def _now() -> float:
    return time.time()


def _ident(value: object, label: str = "ID") -> str:
    text = str(value or "")
    if not IDENTIFIER.fullmatch(text):
        raise ValueError(f"无效的{label}")
    return text


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decoded(value: str | None, default):
    try:
        result = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return result


class AssetLibrary:
    SCHEMA = 1

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.home = self.root / "userdata" / "workbench"
        self.blobs = self.home / "blobs"
        self.waveforms = self.home / "waveforms"
        self.database = self.home / "workbench.db"
        self.home.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.waveforms.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def reading(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    rights_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    current_revision_id TEXT
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
                    parent_revision_id TEXT REFERENCES revisions(id) ON DELETE RESTRICT,
                    blob_sha256 TEXT,
                    blob_suffix TEXT,
                    mime TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS revision_asset_index ON revisions(asset_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS revision_blob_index ON revisions(blob_sha256);
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_assets (
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
                    revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE RESTRICT,
                    role TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(project_id, asset_id, revision_id, role)
                );
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_assets (
                    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(collection_id, asset_id)
                );
                CREATE TABLE IF NOT EXISTS dataset_snapshots (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    training_kind TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS training_runs (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    training_kind TEXT NOT NULL,
                    snapshot_id TEXT REFERENCES dataset_snapshots(id) ON DELETE RESTRICT,
                    state TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    current_job_id TEXT,
                    model_asset_id TEXT REFERENCES assets(id) ON DELETE RESTRICT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)
            stored = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if stored is None:
                db.execute("INSERT INTO meta(key,value) VALUES('schema',?)", (str(self.SCHEMA),))
            elif int(stored["value"]) != self.SCHEMA:
                raise ValueError("资产库版本不受支持，请先完成数据迁移")

    def _blob_path(self, digest: str, suffix: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("无效的素材摘要")
        suffix = suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ".bin"
        directory = self.blobs / digest[:2]
        return within(self.blobs, directory / f"{digest}{suffix}")

    def _promote_blob(self, source: Path) -> tuple[str, str, Path]:
        source = source.resolve()
        if not source.is_file() or source.is_symlink():
            raise ValueError("素材文件不存在或不是普通文件")
        digest = sha256(source)
        suffix = source.suffix.lower() or ".bin"
        destination = self._blob_path(digest, suffix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.stat().st_size != source.stat().st_size or sha256(destination) != digest:
                raise ValueError("资产库中已有同名摘要但内容损坏")
            return digest, suffix, destination
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            if sha256(temporary) != digest:
                raise ValueError("素材复制校验失败")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return digest, suffix, destination

    @staticmethod
    def inspect_audio(path: Path) -> dict:
        """Read lightweight metadata without loading neural models or a full song in memory."""
        metadata: dict[str, object] = {"audio": True}
        try:
            import soundfile as sf
            info = sf.info(str(path))
            metadata.update(duration=round(float(info.duration), 3), sample_rate=int(info.samplerate),
                            channels=int(info.channels), frames=int(info.frames), subtype=str(info.subtype))
        except Exception:
            if path.suffix.lower() == ".wav":
                with wave.open(str(path), "rb") as stream:
                    rate, frames = stream.getframerate(), stream.getnframes()
                    metadata.update(duration=round(frames / rate, 3), sample_rate=rate,
                                    channels=stream.getnchannels(), frames=frames,
                                    subtype=f"PCM_{stream.getsampwidth() * 8}")
        return metadata

    @staticmethod
    def _asset_row(row: sqlite3.Row) -> dict:
        value = dict(row)
        value["tags"] = _decoded(value.pop("tags_json", None), [])
        value["rights"] = _decoded(value.pop("rights_json", None), {})
        value["metadata"] = _decoded(value.pop("metadata_json", None), {})
        value["provenance"] = _decoded(value.pop("provenance_json", None), {})
        return value

    def import_file(self, source: Path, *, kind: str, title: str = "", tags=None,
                    rights=None, provenance=None, metadata=None) -> dict:
        kind = str(kind).strip()
        if kind not in ASSET_KINDS or kind in TEXT_KINDS:
            raise ValueError("素材类型不支持这个文件")
        source = source.resolve()
        if source.suffix.lower() not in AUDIO_SUFFIXES | {".safetensors", ".json", ".zip"}:
            raise ValueError("素材文件格式不受支持")
        digest, suffix, destination = self._promote_blob(source)
        now, asset_id, revision_id = _now(), uuid.uuid4().hex, uuid.uuid4().hex
        title = str(title or source.name).strip()[:200]
        if not title:
            raise ValueError("素材名称不能为空")
        base_metadata = dict(metadata or {})
        if suffix in AUDIO_SUFFIXES:
            base_metadata.update(self.inspect_audio(destination))
        mime = mimetypes.guess_type(destination.name)[0] or "application/octet-stream"
        with self.transaction() as db:
            db.execute("INSERT INTO assets(id,kind,title,tags_json,rights_json,created_at,updated_at,current_revision_id) VALUES(?,?,?,?,?,?,?,?)",
                       (asset_id, kind, title, _json(list(tags or [])), _json(dict(rights or {})),
                        now, now, revision_id))
            db.execute("INSERT INTO revisions(id,asset_id,blob_sha256,blob_suffix,mime,size,metadata_json,provenance_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (revision_id, asset_id, digest, suffix, mime, destination.stat().st_size,
                        _json(base_metadata), _json(dict(provenance or {})), now))
        return self.get_asset(asset_id)

    def create_text(self, *, kind: str, title: str, text: str, tags=None,
                    rights=None, provenance=None, parent_revision_id: str | None = None,
                    asset_id: str | None = None) -> dict:
        if kind not in TEXT_KINDS:
            raise ValueError("文本素材类型无效")
        title, text = str(title).strip()[:200], str(text)
        if not title or len(text.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("文本素材名称为空或内容超过 4 MiB")
        now, revision_id = _now(), uuid.uuid4().hex
        suffix = ".abc" if kind == "score" else ".txt"
        encoded = text.encode("utf-8")
        import hashlib
        digest = hashlib.sha256(encoded).hexdigest()
        destination = self._blob_path(digest, suffix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_bytes(encoded)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        with self.transaction() as db:
            if asset_id:
                asset_id = _ident(asset_id, "素材 ID")
                existing = db.execute("SELECT kind FROM assets WHERE id=? AND status='active'", (asset_id,)).fetchone()
                if existing is None or existing["kind"] != kind:
                    raise ValueError("素材不存在或类型不匹配")
                parent_revision_id = parent_revision_id or db.execute(
                    "SELECT current_revision_id FROM assets WHERE id=?", (asset_id,)).fetchone()[0]
                parent_revision_id = _ident(parent_revision_id, "父版本 ID")
                parent = db.execute("SELECT 1 FROM revisions WHERE id=? AND asset_id=?",
                                    (parent_revision_id, asset_id)).fetchone()
                if parent is None:
                    raise ValueError("父版本不属于当前文本素材")
                db.execute("UPDATE assets SET title=?,updated_at=?,current_revision_id=? WHERE id=?",
                           (title, now, revision_id, asset_id))
            else:
                asset_id = uuid.uuid4().hex
                db.execute("INSERT INTO assets(id,kind,title,tags_json,rights_json,created_at,updated_at,current_revision_id) VALUES(?,?,?,?,?,?,?,?)",
                           (asset_id, kind, title, _json(list(tags or [])), _json(dict(rights or {})),
                            now, now, revision_id))
            db.execute("INSERT INTO revisions(id,asset_id,parent_revision_id,blob_sha256,blob_suffix,mime,size,metadata_json,provenance_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (revision_id, asset_id, parent_revision_id, digest, suffix,
                        "text/vnd.abc" if kind == "score" else "text/plain", len(encoded),
                        _json({"characters": len(text)}), _json(dict(provenance or {})), now))
        return self.get_asset(asset_id)

    def _asset_query(self, *, kind: str = "", query: str = "", project_id: str = "",
                     include_trashed: bool = False) -> tuple[str, list, str]:
        clauses, values = ([] if include_trashed else ["a.status='active'"]), []
        if kind:
            if kind not in ASSET_KINDS:
                raise ValueError("素材类型无效")
            clauses.append("a.kind=?")
            values.append(kind)
        if query:
            clauses.append("(a.title LIKE ? OR a.tags_json LIKE ?)")
            needle = f"%{str(query)[:200]}%"
            values.extend((needle, needle))
        join = ""
        if project_id:
            project_id = _ident(project_id, "项目 ID")
            join = " JOIN project_assets pa ON pa.asset_id=a.id AND pa.revision_id=r.id "
            clauses.append("pa.project_id=?")
            values.append(project_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return join, values, where

    def list_assets(self, *, kind: str = "", query: str = "", project_id: str = "",
                    limit: int = 100, offset: int = 0,
                    include_trashed: bool = False) -> list[dict]:
        join, values, where = self._asset_query(kind=kind, query=query, project_id=project_id,
                                                include_trashed=include_trashed)
        limit = min(500, max(1, int(limit)))
        offset = max(0, int(offset))
        sql = ("SELECT a.*,r.mime,r.size,r.metadata_json,r.provenance_json,r.blob_sha256,r.blob_suffix "
               "FROM assets a JOIN revisions r ON r.id=a.current_revision_id" + join + where +
               " ORDER BY a.updated_at DESC LIMIT ? OFFSET ?")
        with self.reading() as db:
            return [self._asset_row(row) for row in db.execute(sql, (*values, limit, offset)).fetchall()]

    def count_assets(self, *, kind: str = "", query: str = "", project_id: str = "",
                     include_trashed: bool = False) -> int:
        join, values, where = self._asset_query(kind=kind, query=query, project_id=project_id,
                                                include_trashed=include_trashed)
        with self.reading() as db:
            return int(db.execute("SELECT COUNT(DISTINCT a.id) FROM assets a JOIN revisions r "
                                  "ON r.id=a.current_revision_id" + join + where, values).fetchone()[0])

    def get_asset(self, asset_id: str) -> dict:
        asset_id = _ident(asset_id, "素材 ID")
        with self.reading() as db:
            row = db.execute("SELECT a.*,r.mime,r.size,r.metadata_json,r.provenance_json,r.blob_sha256,r.blob_suffix "
                             "FROM assets a JOIN revisions r ON r.id=a.current_revision_id WHERE a.id=?",
                             (asset_id,)).fetchone()
            if row is None:
                raise KeyError(asset_id)
            result = self._asset_row(row)
            result["revisions"] = [dict(item) for item in db.execute(
                "SELECT id,parent_revision_id,mime,size,metadata_json,provenance_json,created_at "
                "FROM revisions WHERE asset_id=? ORDER BY created_at DESC", (asset_id,)).fetchall()]
            for revision in result["revisions"]:
                revision["metadata"] = _decoded(revision.pop("metadata_json"), {})
                revision["provenance"] = _decoded(revision.pop("provenance_json"), {})
            return result

    def revision_file(self, asset_id: str, revision_id: str = "") -> tuple[Path, dict]:
        asset_id = _ident(asset_id, "素材 ID")
        with self.reading() as db:
            if revision_id:
                revision_id = _ident(revision_id, "版本 ID")
                row = db.execute("SELECT * FROM revisions WHERE id=? AND asset_id=?", (revision_id, asset_id)).fetchone()
            else:
                row = db.execute("SELECT r.* FROM assets a JOIN revisions r ON r.id=a.current_revision_id WHERE a.id=?",
                                 (asset_id,)).fetchone()
            if row is None:
                raise KeyError(asset_id)
            info = dict(row)
        path = self._blob_path(info["blob_sha256"], info["blob_suffix"])
        if not path.is_file() or path.stat().st_size != info["size"]:
            raise FileNotFoundError("素材内容缺失或损坏")
        return path, info

    def update_asset(self, asset_id: str, *, title=None, tags=None, status=None) -> dict:
        asset_id = _ident(asset_id, "素材 ID")
        fields, values = [], []
        if title is not None:
            title = str(title).strip()[:200]
            if not title:
                raise ValueError("素材名称不能为空")
            fields.append("title=?")
            values.append(title)
        if tags is not None:
            tags = [str(item).strip()[:50] for item in list(tags)[:30] if str(item).strip()]
            fields.append("tags_json=?")
            values.append(_json(tags))
        if status is not None:
            if status not in {"active", "trashed"}:
                raise ValueError("素材状态无效")
            fields.append("status=?")
            values.append(status)
        if fields:
            fields.append("updated_at=?")
            values.append(_now())
            with self.transaction() as db:
                changed = db.execute(f"UPDATE assets SET {','.join(fields)} WHERE id=?", (*values, asset_id)).rowcount
                if not changed:
                    raise KeyError(asset_id)
        return self.get_asset(asset_id)

    def create_project(self, title: str, metadata=None) -> dict:
        title = str(title).strip()[:160]
        if not title:
            raise ValueError("项目名称不能为空")
        ident, now = uuid.uuid4().hex, _now()
        with self.transaction() as db:
            db.execute("INSERT INTO projects(id,title,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (ident, title, _json(dict(metadata or {})), now, now))
        return self.get_project(ident)

    def update_project(self, project_id: str, *, title=None, status=None, metadata=None) -> dict:
        project_id = _ident(project_id, "项目 ID")
        fields, values = [], []
        if title is not None:
            title = str(title).strip()[:160]
            if not title:
                raise ValueError("项目名称不能为空")
            fields.append("title=?"); values.append(title)
        if status is not None:
            if status not in {"active", "archived"}:
                raise ValueError("项目状态无效")
            fields.append("status=?"); values.append(status)
        if metadata is not None:
            if not isinstance(metadata, dict):
                raise ValueError("项目信息格式无效")
            fields.append("metadata_json=?"); values.append(_json(metadata))
        if fields:
            fields.append("updated_at=?"); values.append(_now()); values.append(project_id)
            with self.transaction() as db:
                if not db.execute(f"UPDATE projects SET {','.join(fields)} WHERE id=?", values).rowcount:
                    raise KeyError(project_id)
        return self.get_project(project_id)

    def list_projects(self, limit: int = 100) -> list[dict]:
        with self.reading() as db:
            rows = db.execute("SELECT p.*,COUNT(pa.asset_id) AS asset_count FROM projects p "
                              "LEFT JOIN project_assets pa ON pa.project_id=p.id WHERE p.status='active' "
                              "GROUP BY p.id ORDER BY p.updated_at DESC LIMIT ?",
                              (min(500, max(1, int(limit))),)).fetchall()
        values = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _decoded(item.pop("metadata_json"), {})
            values.append(item)
        return values

    def get_project(self, project_id: str) -> dict:
        project_id = _ident(project_id, "项目 ID")
        with self.reading() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise KeyError(project_id)
            project = dict(row)
            project["metadata"] = _decoded(project.pop("metadata_json"), {})
            project["assets"] = [dict(item) for item in db.execute(
                "SELECT pa.role,pa.revision_id,pa.created_at,a.id,a.kind,a.title,a.tags_json,r.mime,r.size,r.metadata_json "
                "FROM project_assets pa JOIN assets a ON a.id=pa.asset_id JOIN revisions r ON r.id=pa.revision_id "
                "WHERE pa.project_id=? ORDER BY pa.created_at DESC", (project_id,)).fetchall()]
            for item in project["assets"]:
                item["tags"] = _decoded(item.pop("tags_json"), [])
                item["metadata"] = _decoded(item.pop("metadata_json"), {})
            return project

    def add_to_project(self, project_id: str, asset_id: str, *, revision_id: str = "", role: str = "asset") -> dict:
        project_id, asset_id = _ident(project_id, "项目 ID"), _ident(asset_id, "素材 ID")
        role = str(role).strip()[:50] or "asset"
        with self.transaction() as db:
            project = db.execute("SELECT id FROM projects WHERE id=? AND status='active'", (project_id,)).fetchone()
            asset = db.execute("SELECT current_revision_id FROM assets WHERE id=? AND status='active'", (asset_id,)).fetchone()
            if project is None or asset is None:
                raise ValueError("项目或素材不存在")
            revision_id = _ident(revision_id, "版本 ID") if revision_id else asset["current_revision_id"]
            owned = db.execute("SELECT 1 FROM revisions WHERE id=? AND asset_id=?", (revision_id, asset_id)).fetchone()
            if owned is None:
                raise ValueError("素材版本不属于该素材")
            db.execute("INSERT OR IGNORE INTO project_assets(project_id,asset_id,revision_id,role,created_at) VALUES(?,?,?,?,?)",
                       (project_id, asset_id, revision_id, role, _now()))
            db.execute("UPDATE projects SET updated_at=? WHERE id=?", (_now(), project_id))
        return self.get_project(project_id)

    def remove_from_project(self, project_id: str, asset_id: str, *, revision_id: str = "",
                            role: str = "") -> dict:
        project_id, asset_id = _ident(project_id, "项目 ID"), _ident(asset_id, "素材 ID")
        clauses, values = ["project_id=?", "asset_id=?"], [project_id, asset_id]
        if revision_id:
            clauses.append("revision_id=?"); values.append(_ident(revision_id, "版本 ID"))
        if role:
            clauses.append("role=?"); values.append(str(role).strip()[:50])
        with self.transaction() as db:
            project = db.execute("SELECT metadata_json FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise KeyError(project_id)
            db.execute("DELETE FROM project_assets WHERE " + " AND ".join(clauses), values)
            metadata = _decoded(project["metadata_json"], {})
            master_matches = metadata.get("master_asset_id") == asset_id
            revision_matches = not revision_id or metadata.get("master_revision_id") == revision_id
            if master_matches and revision_matches:
                metadata.pop("master_asset_id", None)
                metadata.pop("master_revision_id", None)
                db.execute("UPDATE projects SET metadata_json=?,updated_at=? WHERE id=?",
                           (_json(metadata), _now(), project_id))
            else:
                db.execute("UPDATE projects SET updated_at=? WHERE id=?", (_now(), project_id))
        return self.get_project(project_id)

    def create_snapshot(self, *, title: str, training_kind: str, items: list[dict], options=None) -> dict:
        if training_kind not in {"yue2_style", "rvc_voice"}:
            raise ValueError("训练类型无效")
        if not items:
            raise ValueError("训练素材不能为空")
        options = dict(options or {})
        if training_kind == "yue2_style" and options.get("rights_confirmed") is not True:
            raise ValueError("请确认你有权使用所选音乐进行训练")
        normalized = []
        with self.reading() as db:
            for raw in items:
                asset_id = _ident(raw.get("asset_id"), "素材 ID")
                revision_id = _ident(raw.get("revision_id"), "版本 ID")
                row = db.execute("SELECT a.kind,a.title,r.metadata_json,r.blob_sha256 FROM assets a JOIN revisions r ON r.asset_id=a.id "
                                 "WHERE a.id=? AND r.id=? AND a.status='active'", (asset_id, revision_id)).fetchone()
                if row is None:
                    raise ValueError("训练素材或固定版本不存在")
                allowed = {"song", "vocal", "work"} if training_kind == "yue2_style" else {"song", "vocal", "reference_voice"}
                if row["kind"] not in allowed:
                    raise ValueError("这个素材类型不能用于所选训练")
                start, end = float(raw.get("start", 0)), raw.get("end")
                duration = float(_decoded(row["metadata_json"], {}).get("duration", 0))
                end = float(end) if end is not None else duration
                if start < 0 or not end > start or (duration and end > duration + .05):
                    raise ValueError("训练片段边界无效")
                lyrics = raw.get("lyrics", "")
                if not isinstance(lyrics, str) or len(lyrics) > 200000:
                    raise ValueError("逐首歌词格式不正确或内容过长")
                style = raw.get("style", "")
                if not isinstance(style, str) or len(style) > 20000:
                    raise ValueError("逐首曲风格式不正确或内容过长")
                normalized.append({
                    "asset_id": asset_id, "revision_id": revision_id, "blob_sha256": row["blob_sha256"],
                    "asset_title": row["title"],
                    "start": start, "end": end, "track_group_id": str(raw.get("track_group_id") or asset_id),
                    "split": "validation" if raw.get("split") == "validation" else "train",
                    "lyrics_revision_id": raw.get("lyrics_revision_id") or None,
                    "lyrics": lyrics.strip(),
                    "style_revision_id": raw.get("style_revision_id") or None,
                    "style": style.strip(),
                    "instrumental": raw.get("instrumental") is True,
                })
                for key, kind in (("lyrics_revision_id", "lyrics"), ("style_revision_id", "style")):
                    linked = normalized[-1][key]
                    if linked:
                        linked = _ident(linked, "文本版本 ID")
                        text_row = db.execute(
                            "SELECT 1 FROM revisions r JOIN assets a ON a.id=r.asset_id "
                            "WHERE r.id=? AND a.kind=? AND a.status='active'", (linked, kind),
                        ).fetchone()
                        if text_row is None:
                            raise ValueError("训练素材关联的歌词或曲风版本无效")
                        normalized[-1][key] = linked
        groups, blobs = {}, {}
        for item in normalized:
            previous_blob = blobs.setdefault(item["blob_sha256"], item)
            if (previous_blob["split"] != item["split"]
                    and previous_blob["asset_id"] != item["asset_id"]):
                raise ValueError(
                    f'“{previous_blob["asset_title"]}”与“{item["asset_title"]}”的音频内容完全相同，'
                    "不能分别作为训练集和验证集；请换一首真正不同的歌曲"
                )
            previous = groups.setdefault(item["track_group_id"], item["split"])
            if previous != item["split"]:
                raise ValueError("同一首歌的衍生素材不能跨训练集与验证集")
        if training_kind == "yue2_style":
            default_lyrics = str(options.get("default_lyrics", "")).strip()
            for item in normalized:
                if not item["instrumental"] and not item["lyrics_revision_id"] and not item["lyrics"] and not default_lyrics:
                    raise ValueError("含人声训练素材必须选择歌词版本、粘贴本曲歌词，或明确标记为纯器乐")
        for item in normalized:
            item.pop("asset_title", None)
        manifest = {"schema": 1, "training_kind": training_kind, "items": normalized,
                    "options": options}
        import hashlib
        encoded = _json(manifest).encode("utf-8")
        ident, now = uuid.uuid4().hex, _now()
        with self.transaction() as db:
            db.execute("INSERT INTO dataset_snapshots(id,title,training_kind,manifest_json,manifest_sha256,created_at) VALUES(?,?,?,?,?,?)",
                       (ident, str(title).strip()[:160] or "训练素材集", training_kind, encoded.decode("utf-8"),
                        hashlib.sha256(encoded).hexdigest(), now))
        return {"id": ident, "title": str(title).strip()[:160] or "训练素材集", **manifest,
                "manifest_sha256": hashlib.sha256(encoded).hexdigest(), "created_at": now}

    def list_snapshots(self, training_kind: str = "") -> list[dict]:
        sql, values = "SELECT * FROM dataset_snapshots", []
        if training_kind:
            sql += " WHERE training_kind=?"
            values.append(training_kind)
        sql += " ORDER BY created_at DESC LIMIT 200"
        with self.reading() as db:
            rows = db.execute(sql, values).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            manifest = _decoded(item.pop("manifest_json"), {})
            result.append({**item, **manifest, "item_count": len(manifest.get("items", []))})
        return result

    def create_training_run(self, *, title: str, training_kind: str, snapshot_id: str,
                            config: dict | None = None) -> dict:
        if training_kind not in {"yue2_style", "rvc_voice"}:
            raise ValueError("训练类型无效")
        snapshot_id = _ident(snapshot_id, "素材快照 ID")
        config = dict(config or {})
        ident, now = uuid.uuid4().hex, _now()
        with self.transaction() as db:
            snapshot = db.execute("SELECT training_kind FROM dataset_snapshots WHERE id=?", (snapshot_id,)).fetchone()
            if snapshot is None or snapshot["training_kind"] != training_kind:
                raise ValueError("训练素材快照不存在或类型不一致")
            db.execute(
                "INSERT INTO training_runs(id,title,training_kind,snapshot_id,state,config_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (ident, str(title).strip()[:160] or "新训练", training_kind, snapshot_id,
                 "draft", _json(config), now, now),
            )
        return self.get_training_run(ident)

    def get_training_run(self, run_id: str) -> dict:
        run_id = _ident(run_id, "训练 ID")
        with self.reading() as db:
            row = db.execute(
                "SELECT tr.*,ds.title AS snapshot_title,ds.manifest_sha256 FROM training_runs tr "
                "JOIN dataset_snapshots ds ON ds.id=tr.snapshot_id WHERE tr.id=?", (run_id,),
            ).fetchone()
        if row is None:
            raise ValueError("训练记录不存在")
        result = dict(row)
        result["config"] = _decoded(result.pop("config_json"), {})
        return result

    def list_training_runs(self, training_kind: str = "") -> list[dict]:
        values = []
        sql = ("SELECT tr.*,ds.title AS snapshot_title,ds.manifest_sha256 FROM training_runs tr "
               "JOIN dataset_snapshots ds ON ds.id=tr.snapshot_id")
        if training_kind:
            if training_kind not in {"yue2_style", "rvc_voice"}:
                raise ValueError("训练类型无效")
            sql += " WHERE tr.training_kind=?"
            values.append(training_kind)
        sql += " ORDER BY tr.updated_at DESC LIMIT 200"
        with self.reading() as db:
            rows = db.execute(sql, values).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["config"] = _decoded(item.pop("config_json"), {})
            result.append(item)
        return result

    def update_training_run(self, run_id: str, *, state: str | None = None,
                            config: dict | None = None, current_job_id: str | None = None,
                            model_asset_id: str | None = None) -> dict:
        run_id = _ident(run_id, "训练 ID")
        allowed_states = {"draft", "preparing", "queued", "running", "pausing", "paused",
                          "complete", "failed", "cancelled"}
        with self.transaction() as db:
            row = db.execute("SELECT * FROM training_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("训练记录不存在")
            updates, values = [], []
            if state is not None:
                if state not in allowed_states:
                    raise ValueError("训练状态无效")
                updates.append("state=?"); values.append(state)
            if config is not None:
                updates.append("config_json=?"); values.append(_json(dict(config)))
            if current_job_id is not None:
                updates.append("current_job_id=?"); values.append(str(current_job_id)[:96] or None)
            if model_asset_id is not None:
                if model_asset_id:
                    model_asset_id = _ident(model_asset_id, "模型素材 ID")
                    model = db.execute("SELECT 1 FROM assets WHERE id=? AND kind='model'", (model_asset_id,)).fetchone()
                    if model is None:
                        raise ValueError("训练产物不是有效的模型资产")
                updates.append("model_asset_id=?"); values.append(model_asset_id or None)
            updates.append("updated_at=?"); values.append(_now())
            values.append(run_id)
            db.execute(f"UPDATE training_runs SET {','.join(updates)} WHERE id=?", values)
        return self.get_training_run(run_id)

    def referenced_job_and_upload_paths(self) -> tuple[set[str], set[str]]:
        """Compatibility hook: managed blobs are outside retention roots by design."""
        return set(), set()
