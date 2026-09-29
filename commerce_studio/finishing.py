"""Non-generative local video finishing for downloaded Flova MP4 files."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .core import ident, stamp


def _tools():
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("本地收尾需要 FFmpeg 和 FFprobe")
    return ffmpeg, ffprobe


def _source_path(store, project, deliverable_id):
    item = next((d for d in project.get("deliverables", []) if d["id"] == deliverable_id and d["kind"] in ("video", "finished_video")), None)
    if not item:
        raise ValueError("镜头来源必须是本地 Flova 成片或已收尾视频")
    path = store.root / "deliverables" / project["id"] / f"{deliverable_id}.mp4"
    if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
        raise ValueError("镜头来源文件校验失败")
    return path


def probe(store, project, deliverable_id):
    _, ffprobe = _tools()
    path = _source_path(store, project, deliverable_id)
    result = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if result.returncode:
        raise ValueError("本地视频无法读取")
    data = json.loads(result.stdout)
    if not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
        raise ValueError("本地文件没有视频轨")
    duration = float(data["format"]["duration"])
    if duration <= 0:
        raise ValueError("视频时长无效")
    return {"duration": duration, "audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


def save_edit(store, project, clips, parent_id=None):
    if not isinstance(clips, list) or not 1 <= len(clips) <= 30:
        raise ValueError("时间线须包含 1 至 30 个片段")
    if parent_id and not any(v["id"] == parent_id for v in project["video_edits"]):
        raise ValueError("收尾父版本不存在")
    cleaned = []
    sources = {}
    for index, clip in enumerate(clips, 1):
        if not isinstance(clip, dict):
            raise ValueError(f"片段 {index} 无效")
        source_id = clip.get("source_id")
        if source_id not in sources:
            sources[source_id] = probe(store, project, source_id)
        try:
            start, end, speed = float(clip.get("start")), float(clip.get("end")), float(clip.get("speed"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"片段 {index} 裁剪或速度无效") from exc
        if not 0 <= start < end <= sources[source_id]["duration"] + 0.05 or not 0.5 <= speed <= 2:
            raise ValueError(f"片段 {index} 超出视频时长或速度范围")
        caption = clip.get("caption") or ""
        if not isinstance(caption, str) or len(caption) > 300:
            raise ValueError(f"片段 {index} 字幕过长")
        cleaned.append({"id": ident(), "source_id": source_id, "start": start, "end": end, "speed": speed,
                        "caption": caption.strip(), "preview_duration": (end - start) / speed})
    version = {"id": ident(), "parent_id": parent_id, "clips": cleaned,
               "preview_duration": sum(c["preview_duration"] for c in cleaned), "created": stamp()}
    project["video_edits"].append(version)
    store.save(project)
    return version


def render(store, project, edit_id):
    version = next((v for v in project["video_edits"] if v["id"] == edit_id), None)
    if not version:
        raise ValueError("收尾版本不存在")
    if any(d.get("edit_id") == edit_id for d in project["deliverables"]):
        raise ValueError("此收尾版本已导出，请编辑后保存新版本")
    ffmpeg, _ = _tools()
    with tempfile.TemporaryDirectory(prefix="commerce-finish-") as temporary:
        folder = Path(temporary)
        outputs = []
        for index, clip in enumerate(version["clips"], 1):
            path = _source_path(store, project, clip["source_id"])
            media = probe(store, project, clip["source_id"])
            output = folder / f"segment-{index:03d}.mp4"
            vf = ("scale=720:1280:force_original_aspect_ratio=decrease,"
                  "pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                  f"setpts=(PTS-STARTPTS)/{clip['speed']}")
            if clip["caption"]:
                caption_file = folder / f"caption-{index:03d}.txt"
                caption_file.write_text(clip["caption"], encoding="utf-8")
                font = "C\\:/Windows/Fonts/msyh.ttc"
                vf += (f",drawtext=fontfile='{font}':textfile='{caption_file.name}':"
                       "fontcolor=white:fontsize=38:box=1:boxcolor=black@0.55:boxborderw=16:"
                       "x=(w-text_w)/2:y=h-170")
            duration = clip["end"] - clip["start"]
            command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", str(clip["start"]), "-t", str(duration), "-i", str(path)]
            if not media["audio"]:
                command += ["-f", "lavfi", "-t", str(duration / clip["speed"]), "-i", "anullsrc=r=48000:cl=stereo"]
            command += ["-vf", vf, "-map", "0:v:0", "-map", "0:a:0" if media["audio"] else "1:a:0"]
            if media["audio"]:
                command += ["-af", f"atempo={clip['speed']}"]
            command += ["-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "22",
                        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(output)]
            done = subprocess.run(command, cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            if done.returncode:
                raise ValueError(f"片段 {index} 收尾失败：{done.stderr[-300:]}")
            outputs.append(output)
        manifest = folder / "concat.txt"
        manifest.write_text("".join(f"file '{path.name}'\n" for path in outputs), encoding="utf-8")
        final = folder / "final.mp4"
        done = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(manifest),
                               "-c", "copy", "-movflags", "+faststart", str(final)], cwd=folder, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=300)
        if done.returncode or not final.is_file() or final.stat().st_size == 0:
            raise ValueError("本地成片合并失败：" + done.stderr[-300:])
        deliverable_id = ident()
        target_dir = store.root / "deliverables" / project["id"]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{deliverable_id}.mp4"
        shutil.copyfile(final, target)
        raw_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    deliverable = {"id": deliverable_id, "kind": "finished_video", "edit_id": edit_id,
                   "name": "commerce-finished.mp4", "bytes": target.stat().st_size, "sha256": raw_hash,
                   "created": stamp()}
    project["deliverables"].append(deliverable)
    store.save(project)
    return deliverable
