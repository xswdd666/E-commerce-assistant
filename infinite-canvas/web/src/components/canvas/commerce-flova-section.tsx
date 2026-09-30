import { useState } from "react";
import { Button, Input, Space, Tag, Typography } from "antd";

import { studioApi, type FlovaPendingAction, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";

type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void>; onInsertVideo: (blob: Blob, title: string, projectId: string, deliverableId: string) => Promise<void> };

function PendingAction({ action, projectId, taskId, busy, act }: { action: FlovaPendingAction; projectId: string; taskId: string; busy: boolean; act: Props["act"] }) {
    const safeUrl = (() => { try { return new URL(action.action_url || "").protocol === "https:" ? action.action_url : null; } catch { return null; } })();
    return <div className="mt-2 rounded border p-2">
        <Typography.Paragraph type="warning" className="mb-1">待确认：{action.message || action.type || "请在 Flova 项目中查看"}</Typography.Paragraph>
        {action.payload != null ? <pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(action.payload, null, 2)}</pre> : null}
        {action.options?.map((option) => <div key={option.id} className="mt-1">
            {option.effect === "resume" ? <Button disabled={busy || !action.action_id || !action.resume_message_id} onClick={() => void act(() => studioApi.flovaResumeAction(projectId, taskId, action.action_id!, option.id))}>{option.label || option.id} · 确认并继续</Button>
                : option.effect === "open_url" && safeUrl ? <a href={safeUrl} target="_blank" rel="noreferrer">{option.label || option.id} · 前往 Flova 完成操作</a>
                    : <Typography.Text>{option.label || option.id} · {option.effect === "none" ? "不执行远端操作" : "请在 Flova 核对操作链接"}</Typography.Text>}
        </div>)}
        {safeUrl && !action.options?.some((option) => option.effect === "open_url") ? <a href={safeUrl} target="_blank" rel="noreferrer">打开 Flova 确认页面</a> : null}
        {!action.options?.length ? <Typography.Text type="secondary">此事项没有可用选项，请在 Flova 项目中核对。</Typography.Text> : null}
    </div>;
}

export function CommerceFlovaSection({ project, busy, act, onInsertVideo }: Props) {
    const [projectId, setProjectId] = useState("");
    const [offer, setOffer] = useState<StudioQuote | null>(null);
    const [shotOffer, setShotOffer] = useState<{ shotId: string; quote: StudioQuote } | null>(null);
    const [exportOffer, setExportOffer] = useState<StudioQuote | null>(null);
    const [resources, setResources] = useState<Array<{ resource_id: string; name?: string; media_type?: string; status?: string }>>([]);
    const linked = project.external.flova_project_id;
    const runs = project.tasks.filter((task) => task.provider === "Flova" && task.kind === "video");
    const storyboard = project.storyboard_versions.find((item) => item.id === project.storyboard_approval);
    const shotRuns = project.tasks.filter((task) => task.provider === "Flova" && task.kind === "video_shot" && task.input_snapshot?.storyboard_id === storyboard?.id);
    const exports = project.tasks.filter((task) => task.provider === "Flova" && task.kind === "video_export");
    const downloadVideo = async (deliverableId: string, name: string) => {
        const blob = await studioApi.videoBlob(project.id, deliverableId);
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${project.name}-${name}`.replace(/[\\/:*?"<>|]/g, "_");
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
            <Typography.Paragraph type="secondary">批准分镜后可一次提交完整创作，或逐镜提交并在 Flova 人工审核后推进下一镜。CLI 通过创作指令约束单镜，控制精度与最终费用仍待真实项目验证。</Typography.Paragraph>
            <Space><Button disabled={busy || !project.storyboard_approval || shotRuns.length > 0} onClick={() => void act(async () => { setOffer(await studioApi.flovaQuote(project.id)); })}>查看整片输入与费用</Button>{offer ? <Button type="primary" disabled={busy || shotRuns.length > 0} onClick={() => void act(async () => { await studioApi.flovaRun(project.id, offer.fingerprint, crypto.randomUUID()); setOffer(null); })}>确认提交整片创作</Button> : null}</Space>
            {offer ? <Typography.Paragraph className="mt-2">分镜：{String((offer.input_snapshot as { storyboard_id: string }).storyboard_id).slice(0, 12)} · {String((offer.input_snapshot as { shot_count: number }).shot_count)} 镜头 · 费用：{offer.estimate == null ? "未知" : offer.estimate}</Typography.Paragraph> : null}
            {storyboard && !runs.some((task) => task.input_snapshot?.storyboard_id === storyboard.id) ? <div className="mt-3 border-t pt-2"><Typography.Text strong>逐镜制作</Typography.Text>{storyboard.shots.map((shot, index) => {
                const latest = shotRuns.filter((task) => task.input_snapshot?.shot_id === shot.id).at(-1);
                const approved = project.shot_approvals?.[shot.id];
                const precedingDone = storyboard.shots.slice(0, index).every((item) => project.shot_approvals?.[item.id]);
                return <div key={shot.id} className="mt-2"><Typography.Paragraph className="mb-1">{index + 1}. {shot.visual} · {shot.duration} 秒 {approved ? <Tag color="success">已审核</Tag> : latest ? <Tag>{latest.status}</Tag> : <Tag>等待</Tag>}</Typography.Paragraph><Space><Button disabled={busy || !precedingDone || Boolean(approved) || Boolean(latest && latest.status !== "失败")} onClick={() => void act(async () => { setShotOffer({ shotId: shot.id, quote: await studioApi.flovaShotQuote(project.id, shot.id) }); })}>{latest?.status === "失败" ? "查看失败镜头重试输入" : "查看单镜输入与费用"}</Button>{latest?.status === "待核对" ? <Button disabled={busy} onClick={() => void act(() => studioApi.flovaRecover(project.id, latest.id))}>恢复远端状态</Button> : null}{latest?.status === "待审核" && !approved ? <Button disabled={busy} onClick={() => void act(() => studioApi.flovaShotApprove(project.id, latest.id))}>已在 Flova 核对，批准此镜</Button> : null}</Space>{shotOffer?.shotId === shot.id ? <div><Typography.Text>本镜 {shot.duration} 秒 · 参考母版：{project.sources.find((source) => source.id === shot.reference_asset_id)?.view_label || "未知"} · 费用：{shotOffer.quote.estimate == null ? "未知" : shotOffer.quote.estimate}</Typography.Text><Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.flovaShotRun(project.id, shot.id, shotOffer.quote.fingerprint, crypto.randomUUID()); setShotOffer(null); })}>确认提交此镜 Flova</Button></div> : null}{latest?.error ? <Typography.Paragraph type="danger">{latest.error}</Typography.Paragraph> : null}{latest?.pending_actions?.map((action, actionIndex) => <PendingAction key={action.action_id || actionIndex} action={action} projectId={project.id} taskId={latest.id} busy={busy} act={act} />)}</div>;
            })}{storyboard.shots.every((shot) => project.shot_approvals?.[shot.id]) && !project.video_approval ? <Button className="mt-2" type="primary" disabled={busy} onClick={() => void act(() => studioApi.flovaShotSequenceApprove(project.id))}>已在 Flova 核对完整时间线，批准导出</Button> : null}</div> : null}
            <div className="mt-3"><Button disabled={busy} onClick={() => void act(async () => undefined)}>刷新本地任务</Button><Button disabled={busy} className="ml-2" onClick={() => void act(async () => { const result = await studioApi.flovaResources(project.id); setResources(result.items); if (result.unparsed) throw new Error("Flova 返回了尚未识别的资源结构，请在 Flova 网页查看"); })}>查看镜头资源</Button></div>
            {resources.map((resource) => <div key={resource.resource_id}><Tag>{resource.media_type || "资源"}</Tag>{resource.name || resource.resource_id} · {resource.status || "状态未知"}{resource.media_type === "video" ? <Button type="link" disabled={busy || project.deliverables.some((item) => item.kind === "shot_video" && item.resource_id === resource.resource_id)} onClick={() => void act(() => studioApi.flovaPullResource(project.id, resource.resource_id))}>{project.deliverables.some((item) => item.kind === "shot_video" && item.resource_id === resource.resource_id) ? "已保存到本机" : "下载镜头"}</Button> : null}</div>)}
            {runs.map((task) => <div key={task.id} className="mt-2"><Tag>{task.status}</Tag>Flova 创作 {task.status === "待核对" ? <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.flovaRecover(project.id, task.id))}>恢复远端状态</Button> : null}{task.status === "待审核" && project.video_approval?.task_id !== task.id ? <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.flovaApprove(project.id, task.id))}>已在 Flova 核对，批准导出</Button> : null}{project.video_approval?.task_id === task.id ? <Tag color="success">已批准</Tag> : null}{task.error ? <Typography.Paragraph type="danger">{task.error}</Typography.Paragraph> : null}{task.pending_actions?.map((action, index) => <PendingAction key={action.action_id || index} action={action} projectId={project.id} taskId={task.id} busy={busy} act={act} />)}</div>)}
            {project.video_approval ? <div className="mt-3 border-t pt-2"><Typography.Text strong>Flova 成片导出</Typography.Text><Typography.Paragraph type="secondary">先核对时间线可导出，再明确提交；完成的 MP4 下载到本机项目目录并进入完整备份。</Typography.Paragraph><Button disabled={busy} onClick={() => void act(async () => { setExportOffer(await studioApi.flovaExportQuote(project.id)); })}>检查导出条件与费用</Button>{exportOffer ? <div className="mt-2">费用：{exportOffer.estimate == null ? "未知" : exportOffer.estimate}<Button type="primary" className="ml-2" disabled={busy} onClick={() => void act(async () => { await studioApi.flovaExportRun(project.id, exportOffer.fingerprint, crypto.randomUUID()); setExportOffer(null); })}>确认导出 Flova 成片</Button></div> : null}</div> : null}
            {exports.map((task) => <div key={task.id} className="mt-2"><Tag>{task.status}</Tag>成片导出 {task.status === "待核对" || task.status === "待下载" ? <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.flovaExportRecover(project.id, task.id))}>恢复导出状态</Button> : null}{task.error ? <Typography.Text type="danger">{task.error}</Typography.Text> : null}</div>)}
            {project.deliverables.filter((item) => ["video", "shot_video", "finished_video"].includes(item.kind)).map((item) => <div key={item.id} className="mt-2"><Tag color="success">{item.kind === "video" ? "Flova 原片" : item.kind === "shot_video" ? "Flova 镜头" : "收尾成片"}</Tag>{item.name} · {(item.bytes / 1024 / 1024).toFixed(1)} MB<Button type="link" disabled={busy} onClick={() => void act(() => downloadVideo(item.id, item.name))}>下载 MP4</Button><Button type="link" disabled={busy} onClick={() => void act(async () => { const blob = await studioApi.videoBlob(project.id, item.id); await onInsertVideo(blob, item.name, project.id, item.id); })}>加入画布视频节点</Button></div>)}
        </>}
    </section>;
}
