"""One handoff package from reviewed gallery and local Flova-derived video."""

from __future__ import annotations

import io
import json
import zipfile

from . import gallery, video_export
from .core import stamp


def export_delivery(store, project, video_id):
    video = next((item for item in project.get("deliverables", []) if item["id"] == video_id and item["kind"] in ("video", "finished_video")), None)
    if not video:
        raise ValueError("请选择已下载的 Flova 视频或其本地收尾版本")
    media = video_export.deliverable_bytes(store, project, video_id)
    gallery_archive = gallery.export_gallery(store, project)
    if not media:
        raise ValueError("视频文件为空")
    adopted = project.get("prompt_adoption")
    prompt = next((v for v in project.get("prompt_versions", []) if adopted and v["id"] == adopted["version_id"]), None)
    script = next((v for v in project.get("script_versions", []) if v["id"] == project.get("script_approval")), None)
    storyboard = next((v for v in project.get("storyboard_versions", []) if v["id"] == project.get("storyboard_approval")), None)
    if not script or not storyboard:
        raise ValueError("交付前请确认脚本和完整分镜")
    if (script["brief_id"] != project["brief_versions"][-1]["id"] or
            script["master_id"] != project["master_versions"][-1]["id"] or
            storyboard["script_id"] != script["id"] or
            storyboard["master_id"] != project["master_versions"][-1]["id"]):
        raise ValueError("脚本或分镜引用旧产品版本，请重新审核")
    manifest = {"format": "commerce-studio-delivery", "version": 1, "created": stamp(),
                "project_id": project["id"], "project_name": project["name"],
                "brief_id": project["brief_versions"][-1]["id"],
                "master_id": project["master_versions"][-1]["id"],
                "script_id": script["id"], "storyboard_id": storyboard["id"],
                "gallery_plan_id": project["gallery_approval"], "video_id": video_id,
                "video_sha256": video["sha256"],
                "prompt_version_id": prompt["id"] if prompt else None,
                "adopted_prompt_source_id": adopted["source_id"] if adopted else None}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        target.writestr("交付/商品详情页视频.mp4", media)
        with zipfile.ZipFile(io.BytesIO(gallery_archive)) as source:
            for name in source.namelist():
                if name.startswith("gallery/"):
                    target.writestr("交付/" + name, source.read(name))
            target.writestr("交付/套图来源清单.json", source.read("source-manifest.json"))
        target.writestr("交付/已确认脚本.txt", script["text"].encode("utf-8"))
        target.writestr("交付/已确认分镜.json", json.dumps(storyboard, ensure_ascii=False, indent=2))
        target.writestr("交付/采用提示词.json", json.dumps(prompt or {}, ensure_ascii=False, indent=2))
        target.writestr("交付/交付清单.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return output.getvalue()
