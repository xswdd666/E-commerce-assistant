import { useState } from "react";
import { Button, Input, Space, Tag, Typography } from "antd";

import { studioApi, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";

type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void> };

export function CommerceFlovaSection({ project, busy, act }: Props) {
    const [projectId, setProjectId] = useState("");
    const [offer, setOffer] = useState<StudioQuote | null>(null);
    const [exportOffer, setExportOffer] = useState<StudioQuote | null>(null);
    const [resources, setResources] = useState<Array<{ resource_id: string; name?: string; media_type?: string; status?: string }>>([]);
    const linked = project.external.flova_project_id;
    const runs = project.tasks.filter((task) => task.provider === "Flova" && task.kind === "video");
    const exports = project.tasks.filter((task) => task.provider === "Flova" && task.kind === "video_export");
    const downloadVideo = async (deliverableId: string) => {
        const blob = await studioApi.videoBlob(project.id, deliverableId);
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${project.name}-成片.mp4`;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    };

    return <section>
        <Typography.Title level={5}>Flova 视频项目</Typography.Title>
        {!linked ? <Space direction="vertical" className="w-full">
            <Typography.Paragraph type="secondary">使用当前商品的一个 Flova 项目。创建或关联不会启动视频生成。</Typography.Paragraph>
            <Button disabled={busy} onClick={() => void act(() => studioApi.flovaCreate(project.id))}>创建 Flova 项目</Button>
            <Space.Compact className="w-full"><Input placeholder="已有 Flova 项目 ID" value={projectId} onChange={(event) => setProjectId(event.target.value)} /><Button disabled={busy || !projectId.trim()} onClick={() => void act(() => studioApi.flovaAttach(project.id, projectId))}>关联</Button></Space.Compact>
        </Space> : <>
            <Typography.Paragraph>项目 ID：{linked} {project.external.flova_project_url ? <a href={project.external.flova_project_url} target="_blank" rel="noreferrer">打开 Flova 精剪</a> : null}</Typography.Paragraph>
            <Typography.Paragraph type="secondary">批准分镜后，工作台上传已确认简报、脚本、分镜及三视图，再启动一次 Flova 聚合创作运行。单镜精度与最终费用仍待真实项目验证。</Typography.Paragraph>
            <Space><Button disabled={busy || !project.storyboard_approval} onClick={() => void act(async () => { setOffer(await studioApi.flovaQuote(project.id)); })}>查看视频输入与费用</Button>{offer ? <Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.flovaRun(project.id, offer.fingerprint, crypto.randomUUID()); setOffer(null); })}>确认提交 Flova</Button> : null}</Space>
            {offer ? <Typography.Paragraph className="mt-2">分镜：{String((offer.input_snapshot as { storyboard_id: string }).storyboard_id).slice(0, 12)} · {String((offer.input_snapshot as { shot_count: number }).shot_count)} 镜头 · 费用：{offer.estimate == null ? "未知" : offer.estimate}</Typography.Paragraph> : null}
            <div className="mt-3"><Button disabled={busy} onClick={() => void act(async () => undefined)}>刷新本地任务</Button><Button disabled={busy} className="ml-2" onClick={() => void act(async () => { const result = await studioApi.flovaResources(project.id); setResources(result.items); if (result.unparsed) throw new Error("Flova 返回了尚未识别的资源结构，请在 Flova 网页查看"); })}>查看镜头资源</Button></div>
            {resources.map((resource) => <div key={resource.resource_id}><Tag>{resource.media_type || "资源"}</Tag>{resource.name || resource.resource_id} · {resource.status || "状态未知"}</div>)}
            {runs.map((task) => <div key={task.id} className="mt-2"><Tag>{task.status}</Tag>Flova 创作 {task.status === "待核对" ? <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.flovaRecover(project.id, task.id))}>恢复远端状态</Button> : null}{task.status === "待审核" && project.video_approval?.task_id !== task.id ? <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.flovaApprove(project.id, task.id))}>已在 Flova 核对，批准导出</Button> : null}{project.video_approval?.task_id === task.id ? <Tag color="success">已批准</Tag> : null}{task.error ? <Typography.Paragraph type="danger">{task.error}</Typography.Paragraph> : null}{task.pending_actions?.map((action, index) => <Typography.Paragraph key={index} type="warning">待确认：{action.message || action.type || "请在 Flova 项目中查看"}</Typography.Paragraph>)}</div>)}
            {project.video_approval ? <div className="mt-3 border-t pt-2"><Typography.Text strong>Flova 成片导出</Typography.Text><Typography.Paragraph type="secondary">先核对时间线可导出，再明确提交；完成的 MP4 下载到本机项目目录并进入完整备份。</Typography.Paragraph><Button disabled={busy} onClick={() => void act(async () => { setExportOffer(await studioApi.flovaExportQuote(project.id)); })}>检查导出条件与费用</Button>{exportOffer ? <div className="mt-2">费用：{exportOffer.estimate == null ? "未知" : exportOffer.estimate}<Button type="primary" className="ml-2" disabled={busy} onClick={() => void act(async () => { await studioApi.flovaExportRun(project.id, exportOffer.fingerprint, crypto.randomUUID()); setExportOffer(null); })}>确认导出 Flova 成片</Button></div> : null}</div> : null}
            {exports.map((task) => <div key={task.id} className="mt-2"><Tag>{task.status}</Tag>成片导出 {task.status === "待核对" || task.status === "待下载" ? <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.flovaExportRecover(project.id, task.id))}>恢复导出状态</Button> : null}{task.error ? <Typography.Text type="danger">{task.error}</Typography.Text> : null}</div>)}
            {project.deliverables.filter((item) => item.kind === "video").map((item) => <div key={item.id} className="mt-2"><Tag color="success">本地成片</Tag>{item.name} · {(item.bytes / 1024 / 1024).toFixed(1)} MB<Button type="link" disabled={busy} onClick={() => void act(() => downloadVideo(item.id))}>下载 MP4</Button></div>)}
        </>}
    </section>;
}
