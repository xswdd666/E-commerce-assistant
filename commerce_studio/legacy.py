"""Read-only import of projects from the previous SQLite prototype."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path


def _read(db_path: Path, project_id: str | None = None):
    if not db_path.is_file():
        return []
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as db:
        if project_id is None:
            rows = db.execute("SELECT state FROM projects ORDER BY updated DESC").fetchall()
        else:
            rows = db.execute("SELECT state FROM projects WHERE id=?", (project_id,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def list_projects(db_path: Path):
    return [{"id": item["id"], "name": item["name"], "sources": len(item.get("sources", [])),
             "facts": len(item.get("facts", [])), "updated": item.get("updated")}
            for item in _read(db_path)]


def import_project(store, db_path: Path, files_root: Path, project_id: str):
    if not isinstance(project_id, str) or not re.fullmatch(r"[0-9a-f]{12}", project_id):
        raise ValueError("旧项目标识无效")
    old = _read(db_path, project_id)
    if not old:
        raise ValueError("旧项目不存在")
    old = old[0]
    existing = next((p for p in store.list() if p.get("legacy_id") == project_id), None)
    if existing:
        return store.load(existing["id"])
    project = store.create(old["name"])
    source_ids = {}
    try:
        for source in old.get("sources", []):
            filename = source.get("path", "")
            if not isinstance(filename, str) or not re.fullmatch(r"[0-9a-f]{12}\.[A-Za-z0-9]{1,12}", filename):
                raise ValueError("旧项目文件路径无效")
            path = files_root / project_id / filename
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != source.get("sha256"):
                raise ValueError(f"旧项目文件校验失败：{filename}")
            imported = store.add_source(project, source["name"], source["mime"], base64.b64encode(raw).decode("ascii"))
            imported["origin"] = "旧原型导入"
            source_ids[source["id"]] = imported["id"]
        for fact in old.get("facts", []):
            old_sources = fact.get("source_ids") or []
            status = fact.get("status") if fact.get("status") in ("已知事实", "待核实", "创意假设") else "待核实"
            imported = store.add_fact(project, fact["field"], fact["value"], source_ids.get(old_sources[0]) if old_sources else None, status)
            imported["legacy_fact_id"] = fact["id"]
        project["legacy_import"] = {"id": project_id, "imported_at": project["updated"],
                                    "review_required": "请重新确认三视图母版和下游成品",
                                    "archive": copy.deepcopy(old)}
        project["legacy_id"] = project_id
        store.save(project)
        return project
    except Exception:
        generated = store.root / "files" / project["id"]
        for source in project["sources"]:
            (generated / source["id"]).unlink(missing_ok=True)
        if generated.exists():
            generated.rmdir()
        store._path(project["id"]).unlink(missing_ok=True)
        raise
