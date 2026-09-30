"""Portable companion-state archive for one linked commerce project."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

from .core import ident, stamp


FORMAT = "commerce-studio-project"
VERSION = 1


def _verified_file(path, expected):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError("备份文件校验失败")


def write_project_archive(store, project, destination):
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("manifest.json", json.dumps({"format": FORMAT, "version": VERSION,
                                                       "project_id": project["id"], "exported_at": stamp()}, ensure_ascii=False))
        output.writestr("project.json", json.dumps(project, ensure_ascii=False, indent=2))
        for source in project["sources"]:
            path = store.root / "files" / project["id"] / source["id"]
            _verified_file(path, source["sha256"])
            output.write(path, f"files/{source['id']}")
        for deliverable in project.get("deliverables", []):
            if deliverable.get("kind") not in ("video", "shot_video", "finished_video"):
                raise ValueError("备份包含未知交付物类型")
            path = store.root / "deliverables" / project["id"] / f"{deliverable['id']}.mp4"
            _verified_file(path, deliverable.get("sha256"))
            output.write(path, f"deliverables/{deliverable['id']}.mp4")


def export_project(store, project):
    archive = io.BytesIO()
    write_project_archive(store, project, archive)
    return archive.getvalue()


def _copy_verified(archive, info, target, expected, error):
    digest = hashlib.sha256()
    with archive.open(info) as source, target.open("wb") as output:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError(error)


def restore_project(store, raw):
    if isinstance(raw, bytes):
        if not raw or len(raw) > 250 * 1024 * 1024:
            raise ValueError("备份为空或超过 250 MB")
        source = io.BytesIO(raw)
    elif isinstance(raw, Path):
        if not raw.is_file() or not 0 < raw.stat().st_size <= 250 * 1024 * 1024:
            raise ValueError("备份为空或超过 250 MB")
        source = raw
    else:
        raise ValueError("备份格式无效")
    try:
        with zipfile.ZipFile(source) as archive, tempfile.TemporaryDirectory(dir=store.root) as temporary:
            temporary = Path(temporary)
            if len(archive.infolist()) > 1000:
                raise ValueError("备份文件数量过多")
            if sum(info.file_size for info in archive.infolist()) > 250 * 1024 * 1024:
                raise ValueError("备份展开后超过 250 MB")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
                raise ValueError("备份格式或版本不受支持")
            original = json.loads(archive.read("project.json"))
            if not isinstance(original, dict) or original.get("id") != manifest.get("project_id") or not isinstance(original.get("sources"), list):
                raise ValueError("备份中的项目状态无效")
            source_files = {}
            for source in original["sources"]:
                source_id = source.get("id")
                if not isinstance(source_id, str) or len(source_id) != 32 or any(c not in "0123456789abcdef" for c in source_id):
                    raise ValueError("备份包含无效来源标识")
                if source_id in source_files:
                    raise ValueError("备份中的来源文件标识重复")
                info = archive.getinfo(f"files/{source_id}")
                if info.file_size > 25 * 1024 * 1024:
                    raise ValueError("备份中的来源文件过大")
                path = temporary / source_id
                _copy_verified(archive, info, path, source.get("sha256"), "备份来源文件校验失败")
                source_files[source_id] = path
            deliverable_files = {}
            for deliverable in original.get("deliverables", []):
                deliverable_id = deliverable.get("id")
                if not isinstance(deliverable_id, str) or len(deliverable_id) != 32 or any(c not in "0123456789abcdef" for c in deliverable_id):
                    raise ValueError("备份包含无效交付物标识")
                if deliverable_id in deliverable_files:
                    raise ValueError("备份交付物标识重复")
                path = temporary / f"{deliverable_id}.mp4"
                _copy_verified(archive, f"deliverables/{deliverable_id}.mp4", path,
                               deliverable.get("sha256"), "备份交付物校验失败")
                deliverable_files[deliverable_id] = path
            project = copy.deepcopy(original)
            project["restored_from"] = original["id"]
            project["id"] = ident()
            project["restored_at"] = stamp()
            for task in project.get("tasks", []):
                if task.get("status") in ("已排队", "上传素材", "远端运行中", "待用户确认"):
                    task["status"] = "待核对"
                    task["updated"] = stamp()
            directory = store.root / "files" / project["id"]
            directory.mkdir(parents=True, exist_ok=False)
            for source_id, path in source_files.items():
                path.replace(directory / source_id)
            if deliverable_files:
                video_directory = store.root / "deliverables" / project["id"]
                video_directory.mkdir(parents=True, exist_ok=False)
                for deliverable_id, path in deliverable_files.items():
                    path.replace(video_directory / f"{deliverable_id}.mp4")
            store.save(project)
            return project
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValueError("备份 ZIP 缺少必要文件或内容已损坏") from exc
