import { useEffect, useState } from "react";
import { Button, Input, Select, Space, Tag, Typography } from "antd";

import { studioApi, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";

type ShotDraft = { visual: string; duration: number; reference_asset_id: string; caption: string };
type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void> };

export function CommercePlanSection({ project, busy, act }: Props) {
    const [offer, setOffer] = useState<StudioQuote | null>(null);
    const [script, setScript] = useState("");
    const [shots, setShots] = useState<ShotDraft[]>([{ visual: "", duration: 5, reference_asset_id: "", caption: "" }]);
    const master = project.master_versions.at(-1);
    const scriptVersion = project.script_versions.at(-1);
    const storyboard = project.storyboard_versions.at(-1);

    useEffect(() => {
        if (!master?.asset_ids[0]) return;
        setShots((current) => current.map((shot) => shot.reference_asset_id ? shot : { ...shot, reference_asset_id: master.asset_ids[0] }));
    }, [master?.id]);

    const updateShot = (index: number, patch: Partial<ShotDraft>) => setShots((current) => current.map((shot, i) => i === index ? { ...shot, ...patch } : shot));

    return <section>
        <Typography.Title level={5}>广告方向、脚本与分镜</Typography.Title>
        <Typography.Paragraph type="secondary">所有正式步骤读取已确认简报与母版。先选方向，再批准脚本和完整分镜。</Typography.Paragraph>
        <Space>
            <Button disabled={busy || !master} onClick={() => void act(async () => { setOffer(await studioApi.directionsQuote(project.id)); })}>查看三种方向的输入与费用</Button>
            {offer ? <Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.directionsRun(project.id, offer.fingerprint, crypto.randomUUID()); setOffer(null); })}>确认生成</Button> : null}
        </Space>
        {offer ? <Typography.Paragraph className="mt-2">DeepSeek 预估费用：{offer.estimate == null ? "未知" : offer.estimate}；输入简报：{String((offer.input_snapshot as { brief_id: string }).brief_id).slice(0, 12)}</Typography.Paragraph> : null}
        {project.directions.map((direction) => <div key={direction.id} className="mt-3 border-t pt-2">
            <Typography.Text strong>{direction.title}</Typography.Text> {project.direction_approval === direction.id ? <Tag color="success">当前主线</Tag> : null}
            <Typography.Paragraph className="mb-1">目标用户：{direction.audience}<br />开头：{direction.opening}<br />卖点：{direction.selling_point}<br />结尾：{direction.ending}</Typography.Paragraph>
            <Button type="link" disabled={busy || project.direction_approval === direction.id} onClick={() => void act(() => studioApi.approveDirection(project.id, direction.id))}>选为广告主线</Button>
        </div>)}

        {project.direction_approval ? <div className="mt-4">
            <Typography.Text strong>脚本</Typography.Text>
            <Input.TextArea rows={5} className="mt-2" placeholder="按选定主线编辑完整脚本" value={script} onChange={(event) => setScript(event.target.value)} />
            <Space className="mt-2"><Button disabled={busy || !script.trim()} onClick={() => void act(async () => { await studioApi.script(project.id, script); setScript(""); })}>保存脚本新版本</Button>{scriptVersion ? <Button type="primary" disabled={busy || project.script_approval === scriptVersion.id} onClick={() => void act(() => studioApi.approveScript(project.id, scriptVersion.id))}>批准最新脚本</Button> : null}</Space>
            {scriptVersion ? <Typography.Paragraph className="mt-2 whitespace-pre-wrap">最新脚本：{scriptVersion.text}</Typography.Paragraph> : null}
        </div> : null}

        {project.script_approval && master ? <div className="mt-4">
            <Typography.Text strong>分镜</Typography.Text>
            {shots.map((shot, index) => <Space direction="vertical" key={index} className="mt-2 w-full border-t pt-2">
                <Typography.Text>镜头 {index + 1}</Typography.Text>
                <Input.TextArea placeholder="画面描述" value={shot.visual} onChange={(event) => updateShot(index, { visual: event.target.value })} />
                <Space><Input type="number" min={0.5} max={15} step={0.5} value={shot.duration} onChange={(event) => updateShot(index, { duration: Number(event.target.value) })} addonAfter="秒" /><Button disabled={shots.length === 1} onClick={() => setShots((current) => current.filter((_, i) => i !== index))}>删除镜头</Button></Space>
                <Select value={shot.reference_asset_id || undefined} onChange={(reference_asset_id) => updateShot(index, { reference_asset_id })} options={master.asset_ids.map((id) => ({ value: id, label: project.sources.find((source) => source.id === id)?.view_label || id }))} />
                <Input placeholder="字幕或静音说明" value={shot.caption} onChange={(event) => updateShot(index, { caption: event.target.value })} />
            </Space>)}
            <Typography.Paragraph className="mt-2">总时长：{shots.reduce((sum, shot) => sum + (Number.isFinite(shot.duration) ? shot.duration : 0), 0).toFixed(1)} 秒</Typography.Paragraph>
            <Space><Button onClick={() => setShots((current) => [...current, { visual: "", duration: 3, reference_asset_id: master.asset_ids[0], caption: "" }])}>增加镜头</Button><Button disabled={busy || shots.some((shot) => !shot.visual.trim() || !shot.reference_asset_id)} onClick={() => void act(() => studioApi.storyboard(project.id, shots))}>保存分镜新版本</Button>{storyboard ? <Button type="primary" disabled={busy || project.storyboard_approval === storyboard.id} onClick={() => void act(() => studioApi.approveStoryboard(project.id, storyboard.id))}>批准最新分镜</Button> : null}</Space>
            {storyboard ? <div className="mt-2"><Typography.Text>最新版本 {storyboard.shots.length} 镜头 · {storyboard.shots.reduce((sum, shot) => sum + shot.duration, 0).toFixed(1)} 秒 {project.storyboard_approval === storyboard.id ? "（已批准）" : "（待批准）"}</Typography.Text>{storyboard.shots.map((shot, index) => <Typography.Paragraph key={shot.id} className="mt-1">{index + 1}. {shot.visual} · {shot.duration} 秒 · {project.sources.find((source) => source.id === shot.reference_asset_id)?.view_label || "参考未知"}{shot.caption ? ` · 字幕：${shot.caption}` : ""}</Typography.Paragraph>)}</div> : null}
        </div> : null}
    </section>;
}
