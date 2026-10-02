import { useEffect, useState } from "react";
import { Button, Input, InputNumber, Select, Space, Tag, Typography } from "antd";

import { studioApi, type StudioProject } from "@/services/api/commerce-studio";

type Clip = { key: string; source_id: string; start: number; end: number; speed: number; caption: string };
type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void> };

export function CommerceFinishingSection({ project, busy, act }: Props) {
    const latest = project.video_edits.at(-1);
    const videos = project.deliverables.filter((item) => ["video", "shot_video", "finished_video"].includes(item.kind));
    const [sourceId, setSourceId] = useState<string>();
    const [clips, setClips] = useState<Clip[]>([]);
    const [previewUrl, setPreviewUrl] = useState<string>();
    const [localError, setLocalError] = useState("");

    useEffect(() => { setSourceId((current) => current || videos[0]?.id); }, [videos[0]?.id]);
    useEffect(() => {
        if (latest) setClips(latest.clips.map((clip) => ({ ...clip, key: clip.id })));
    }, [latest?.id]);
    useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

    const dirty = latest ? JSON.stringify(clips.map(({ source_id, start, end, speed, caption }) => ({ source_id, start, end, speed, caption }))) !==
        JSON.stringify(latest.clips.map(({ source_id, start, end, speed, caption }) => ({ source_id, start, end, speed, caption }))) : clips.length > 0;
    const duration = clips.reduce((sum, clip) => sum + (clip.end - clip.start) / clip.speed, 0);
    const update = (key: string, patch: Partial<Clip>) => setClips((current) => current.map((clip) => clip.key === key ? { ...clip, ...patch } : clip));
    const move = (index: number, delta: number) => setClips((current) => {
        const next = [...current];
        const other = index + delta;
        if (other < 0 || other >= next.length) return current;
        [next[index], next[other]] = [next[other], next[index]];
        return next;
    });
    const addClip = async () => {
        if (!sourceId) return;
        try {
            const info = await studioApi.videoProbe(project.id, sourceId);
            setClips((current) => [...current, { key: crypto.randomUUID(), source_id: sourceId, start: 0,
                end: Math.min(info.duration, 15), speed: 1, caption: "" }]);
            setLocalError("");
        } catch (cause) { setLocalError(cause instanceof Error ? cause.message : "无法读取视频时长"); }
    };
    const preview = async (deliverableId: string) => {
        try {
            const blob = await studioApi.videoBlob(project.id, deliverableId);
            setPreviewUrl(URL.createObjectURL(blob));
            setLocalError("");
        } catch (cause) { setLocalError(cause instanceof Error ? cause.message : "无法预览视频"); }
    };

    return <section>
        <Typography.Title level={5}>本地视频收尾</Typography.Title>
        <Typography.Paragraph type="secondary">基于已下载的 Flova 成片或单镜素材裁剪、重排、变速和加字幕；不会再次请求生成模型。音频随速度调整时保持音高。</Typography.Paragraph>
        {localError ? <Typography.Text type="danger">{localError}</Typography.Text> : null}
        {!videos.length ? <Typography.Text type="secondary">Flova 成片下载后可编辑。</Typography.Text> : <>
            <Space.Compact className="w-full"><Select className="w-full" value={sourceId} onChange={setSourceId} options={videos.map((item) => ({ value: item.id, label: item.kind === "video" ? "Flova 原片" : item.kind === "shot_video" ? `镜头 ${item.name}` : `收尾版本 ${item.id.slice(0, 8)}` }))} /><Button disabled={busy || !sourceId} onClick={() => void addClip()}>添加片段</Button></Space.Compact>
            {clips.map((clip, index) => <div key={clip.key} className="mt-3 border-t pt-2">
                <Space><Typography.Text strong>片段 {index + 1}</Typography.Text><Button size="small" disabled={index === 0} onClick={() => move(index, -1)}>上移</Button><Button size="small" disabled={index === clips.length - 1} onClick={() => move(index, 1)}>下移</Button><Button size="small" onClick={() => setClips((current) => current.filter((item) => item.key !== clip.key))}>删除</Button></Space>
                <Typography.Paragraph className="mt-1 mb-1" type="secondary">来源：{videos.find((item) => item.id === clip.source_id)?.name || clip.source_id}</Typography.Paragraph>
                <Space wrap><Typography.Text>起点</Typography.Text><InputNumber min={0} step={0.1} value={clip.start} onChange={(start) => update(clip.key, { start: start ?? 0 })} addonAfter="秒" /><Typography.Text>终点</Typography.Text><InputNumber min={0.1} step={0.1} value={clip.end} onChange={(end) => update(clip.key, { end: end ?? 1 })} addonAfter="秒" /><Typography.Text>速度</Typography.Text><InputNumber min={0.5} max={2} step={0.1} value={clip.speed} onChange={(speed) => update(clip.key, { speed: speed ?? 1 })} addonAfter="×" /></Space>
                <Input className="mt-2" placeholder="可选字幕" value={clip.caption} onChange={(event) => update(clip.key, { caption: event.target.value })} />
            </div>)}
            <Typography.Paragraph className="mt-2">预计总时长：{Number.isFinite(duration) ? duration.toFixed(1) : "—"} 秒 · 竖屏 720×1280</Typography.Paragraph>
            <Space wrap><Button disabled={busy || !clips.length || clips.some((clip) => clip.start < 0 || clip.end <= clip.start || clip.speed < 0.5 || clip.speed > 2)} onClick={() => void act(() => studioApi.videoEditSave(project.id, clips.map(({ source_id, start, end, speed, caption }) => ({ source_id, start, end, speed, caption })), latest?.id || null))}>保存收尾新版本</Button><Button type="primary" disabled={busy || !latest || dirty} onClick={() => void act(() => studioApi.videoEditRender(project.id, latest!.id))}>本机渲染已保存版本</Button></Space>
            {latest ? <Typography.Paragraph className="mt-2">已保存版本 {latest.id.slice(0, 8)} · {latest.preview_duration.toFixed(1)} 秒 {dirty ? <Tag>当前编辑未保存</Tag> : null}</Typography.Paragraph> : null}
            {videos.map((item) => <div key={item.id} className="mt-1"><Tag color={item.kind === "finished_video" ? "success" : "default"}>{item.kind === "finished_video" ? "收尾成片" : "Flova 原片"}</Tag>{item.name}<Button type="link" onClick={() => void preview(item.id)}>预览</Button></div>)}
            {previewUrl ? <video className="mt-2 w-full" controls src={previewUrl} /> : null}
        </>}
    </section>;
}
