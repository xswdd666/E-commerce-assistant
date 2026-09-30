import { useEffect, useState } from "react";
import { Button, Input, Select, Space, Tag, Typography } from "antd";

import { studioApi, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";

type ShotDraft = { visual: string; duration: number; reference_asset_id: string; scene_source_id: string | null; ratio: string; caption: string; detail_ids: string[] };
type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void>; onInsertText: (text: string, title: string, source?: { projectId: string; kind: "script" | "storyboard"; versionId: string }) => void };

export function CommercePlanSection({ project, busy, act, onInsertText }: Props) {
    const [offer, setOffer] = useState<StudioQuote | null>(null);
    const [jevOffer, setJevOffer] = useState<StudioQuote | null>(null);
    const [script, setScript] = useState("");
    const [shots, setShots] = useState<ShotDraft[]>([{ visual: "", duration: 5, reference_asset_id: "", scene_source_id: null, ratio: "9:16", caption: "", detail_ids: [] }]);
    const master = project.master_versions.at(-1);
    const scriptVersion = project.script_versions.at(-1);
    const storyboard = project.storyboard_versions.at(-1);
    const approvedScript = project.script_versions.find((item) => item.id === project.script_approval);
    const approvedStoryboard = project.storyboard_versions.find((item) => item.id === project.storyboard_approval);
    const adoptedSceneIds = new Set([project.prompt_adoption?.source_id, ...Object.values(project.canvas_preview_adoption), ...Object.values(project.gallery_choices)].filter((id): id is string => Boolean(id)));
    const sceneOptions = project.sources.filter((source) => adoptedSceneIds.has(source.id)).map((source) => ({ value: source.id, label: source.name }));

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
        {project.directions.length >= 3 ? <div className="mt-4 border-t pt-3">
            <Typography.Text strong>Jev 旁路判断</Typography.Text>
            <Typography.Paragraph type="secondary">Jev 只比较三个广告方向，不批准方向或后续生成。每次提交由你确认，结果与实际选择分别留痕。</Typography.Paragraph>
            <Button disabled={busy} onClick={() => void act(async () => { setJevOffer(await studioApi.jevDirectionsQuote(project.id)); })}>查看 Jev 输入与费用</Button>
            {jevOffer ? <div className="mt-2"><Typography.Paragraph>已确认简报与三个广告方向 · 模型 jev-latest · 费用：未知；{jevOffer.pricing_source}</Typography.Paragraph><Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.jevDirectionsRun(project.id, jevOffer.fingerprint, crypto.randomUUID()); setJevOffer(null); })}>确认提交 Jev 旁路判断</Button></div> : null}
            {project.jev_observations.map((observation) => <div key={observation.id} className="mt-2 border-t pt-2"><Typography.Paragraph className="mb-1">Jev 选择：{project.directions.find((direction) => direction.id === observation.choice_id)?.title || observation.choice_id}；置信度 {(observation.confidence * 100).toFixed(1)}%；模型 {observation.model}</Typography.Paragraph>{observation.direction_ids.map((id) => <Tag key={id}>{project.directions.find((direction) => direction.id === id)?.title || id}：{((observation.probabilities[id] || 0) * 100).toFixed(1)}%</Tag>)}<Typography.Paragraph className="mt-1" type="secondary">{observation.comparisons.length ? observation.comparisons.map((comparison) => `${project.directions.find((direction) => direction.id === comparison.direction_id)?.title || comparison.direction_id}：${comparison.agrees ? "与 Jev 一致" : "与 Jev 不同"}`).join("；") : "等待人工选择广告方向"}</Typography.Paragraph></div>)}
        </div> : null}

        {project.direction_approval ? <div className="mt-4">
            <Typography.Text strong>脚本</Typography.Text>
            <Input.TextArea rows={5} className="mt-2" placeholder="按选定主线编辑完整脚本" value={script} onChange={(event) => setScript(event.target.value)} />
            <Space className="mt-2"><Button disabled={busy || !script.trim()} onClick={() => void act(async () => { await studioApi.script(project.id, script); setScript(""); })}>保存脚本新版本</Button>{scriptVersion ? <Button type="primary" disabled={busy || project.script_approval === scriptVersion.id} onClick={() => void act(() => studioApi.approveScript(project.id, scriptVersion.id))}>批准最新脚本</Button> : null}</Space>
            {scriptVersion ? <Typography.Paragraph className="mt-2 whitespace-pre-wrap">最新脚本：{scriptVersion.text}</Typography.Paragraph> : null}
            {approvedScript ? <Button type="link" onClick={() => onInsertText(approvedScript.text, "已批准广告脚本", { projectId: project.id, kind: "script", versionId: approvedScript.id })}>把已批准脚本加入画布</Button> : null}
        </div> : null}

        {project.script_approval && master ? <div className="mt-4">
            <Typography.Text strong>分镜</Typography.Text>
            {shots.map((shot, index) => <Space direction="vertical" key={index} className="mt-2 w-full border-t pt-2">
                <Typography.Text>镜头 {index + 1}</Typography.Text>
                <Input.TextArea placeholder="画面描述" value={shot.visual} onChange={(event) => updateShot(index, { visual: event.target.value })} />
                <Space><Input type="number" min={0.5} max={15} step={0.5} value={shot.duration} onChange={(event) => updateShot(index, { duration: Number(event.target.value) })} addonAfter="秒" /><Button disabled={shots.length === 1} onClick={() => setShots((current) => current.filter((_, i) => i !== index))}>删除镜头</Button></Space>
                <Select value={shot.reference_asset_id || undefined} onChange={(reference_asset_id) => updateShot(index, { reference_asset_id })} options={master.asset_ids.map((id) => ({ value: id, label: project.sources.find((source) => source.id === id)?.view_label || id }))} />
                <Select className="w-full" placeholder="此镜头的已采用场景图（可选）" allowClear value={shot.scene_source_id || undefined} onChange={(scene_source_id) => updateShot(index, { scene_source_id: scene_source_id || null })} options={sceneOptions} />
                <Select className="w-full" value={shot.ratio} onChange={(ratio) => updateShot(index, { ratio })} options={["9:16", "16:9", "1:1"].map((ratio) => ({ value: ratio, label: `画幅 ${ratio}` }))} />
                {master.inferred_details.some((detail) => typeof detail !== "string") ? <Select mode="multiple" className="w-full" placeholder="此镜头涉及的推断结构（特写或卖点须逐项核实）" value={shot.detail_ids} onChange={(detail_ids) => updateShot(index, { detail_ids })} options={master.inferred_details.filter((detail) => typeof detail !== "string").map((detail) => ({ value: detail.id, label: `${detail.text} · ${detail.status}` }))} /> : null}
                <Input placeholder="字幕或静音说明" value={shot.caption} onChange={(event) => updateShot(index, { caption: event.target.value })} />
            </Space>)}
            <Typography.Paragraph className="mt-2">总时长：{shots.reduce((sum, shot) => sum + (Number.isFinite(shot.duration) ? shot.duration : 0), 0).toFixed(1)} 秒</Typography.Paragraph>
            <Space><Button onClick={() => setShots((current) => [...current, { visual: "", duration: 3, reference_asset_id: master.asset_ids[0], scene_source_id: null, ratio: "9:16", caption: "", detail_ids: [] }])}>增加镜头</Button><Button disabled={busy || shots.some((shot) => !shot.visual.trim() || !shot.reference_asset_id)} onClick={() => void act(() => studioApi.storyboard(project.id, shots))}>保存分镜新版本</Button>{storyboard ? <Button type="primary" disabled={busy || project.storyboard_approval === storyboard.id} onClick={() => void act(() => studioApi.approveStoryboard(project.id, storyboard.id))}>批准最新分镜</Button> : null}</Space>
            {storyboard ? <div className="mt-2"><Typography.Text>最新版本 {storyboard.shots.length} 镜头 · {storyboard.shots.reduce((sum, shot) => sum + shot.duration, 0).toFixed(1)} 秒 {project.storyboard_approval === storyboard.id ? "（已批准）" : "（待批准）"}</Typography.Text>{storyboard.shots.map((shot, index) => <Typography.Paragraph key={shot.id} className="mt-1">{index + 1}. {shot.visual} · {shot.duration} 秒 · {shot.ratio || "9:16"} · {project.sources.find((source) => source.id === shot.reference_asset_id)?.view_label || "参考未知"}{shot.scene_source_id ? ` · 场景图：${project.sources.find((source) => source.id === shot.scene_source_id)?.name || "未知"}` : ""}{shot.caption ? ` · 字幕：${shot.caption}` : ""}</Typography.Paragraph>)}</div> : null}
            {approvedStoryboard ? <Button type="link" onClick={() => onInsertText(approvedStoryboard.shots.map((shot, index) => `${index + 1}. ${shot.visual}；${shot.duration} 秒；${shot.ratio || "9:16"}；母版图 ${shot.reference_asset_id}；场景图 ${shot.scene_source_id || "无"}；字幕 ${shot.caption || "无"}`).join("\n"), "已批准完整分镜", { projectId: project.id, kind: "storyboard", versionId: approvedStoryboard.id })}>把已批准分镜加入画布</Button> : null}
        </div> : null}
    </section>;
}
