import { useState } from "react";
import { Button, Input, Space, Tag, Typography } from "antd";

import { studioApi, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";

type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void> };

export function CommerceFlovaSection({ project, busy, act }: Props) {
    const [projectId, setProjectId] = useState("");
    const [offer, setOffer] = useState<StudioQuote | null>(null);
    const linked = project.external.flova_project_id;
    const runs = project.tasks.filter((task) => task.provider === "Flova");

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
            <div className="mt-3"><Button disabled={busy} onClick={() => void act(async () => undefined)}>刷新本地任务</Button></div>
            {runs.map((task) => <div key={task.id} className="mt-2"><Tag>{task.status}</Tag>Flova 创作 {task.status === "待核对" ? <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.flovaRecover(project.id, task.id))}>恢复远端状态</Button> : null}{task.pending_actions?.map((action, index) => <Typography.Paragraph key={index} type="warning">待确认：{action.message || action.type || "请在 Flova 项目中查看"}</Typography.Paragraph>)}</div>)}
        </>}
    </section>;
}
