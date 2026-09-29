import { useEffect, useState } from "react";
import { Button, Drawer, Input, Select, Space, Tag, Typography } from "antd";
import localforage from "localforage";

import { studioApi, type MasterRequest, type PreviewRequest, type StudioFact, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";
import { buildNodeGenerationContext, hydrateNodeGenerationContext } from "@/components/canvas/canvas-node-generation";
import { CanvasNodeType } from "@/types/canvas";
import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";

type Props = { open: boolean; onClose: () => void; canvasId: string; title: string; nodes: CanvasNodeData[]; connections: CanvasConnection[]; selectedNodeId?: string; onInsertImage: (dataUrl: string, title: string) => Promise<void> };

export function CommerceStudioPanel({ open, onClose, canvasId, title, nodes, connections, selectedNodeId, onInsertImage }: Props) {
    const [project, setProject] = useState<StudioProject | null>(null);
    const [health, setHealth] = useState<{ deepseek: boolean; seeany: boolean; flova: boolean } | null>(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [field, setField] = useState("");
    const [value, setValue] = useState("");
    const [sourceId, setSourceId] = useState<string>();
    const [factStatus, setFactStatus] = useState<StudioFact["status"]>("待核实");
    const [masterIds, setMasterIds] = useState<string[]>([]);
    const [candidateGroup, setCandidateGroup] = useState(1);
    const [viewLabel, setViewLabel] = useState("正面");
    const [viewPreset, setViewPreset] = useState("front");
    const [candidateSourceId, setCandidateSourceId] = useState<string>();
    const [masterQuote, setMasterQuote] = useState<StudioQuote | null>(null);
    const [chatPrompt, setChatPrompt] = useState("");
    const [chatQuote, setChatQuote] = useState<StudioQuote | null>(null);
    const [previewQuote, setPreviewQuote] = useState<StudioQuote | null>(null);
    const [previewRequest, setPreviewRequest] = useState<PreviewRequest | null>(null);
    const [previewRatio, setPreviewRatio] = useState("1:1");
    const [comparison, setComparison] = useState<{ original: string; generated: string } | null>(null);

    useEffect(() => { setPreviewQuote(null); setPreviewRequest(null); }, [nodes, connections, selectedNodeId, previewRatio]);

    useEffect(() => {
        if (!open) return;
        void (async () => {
            try {
                setHealth(await studioApi.health());
                const id = await localforage.getItem<string>(`commerce-studio:${canvasId}`);
                setProject(id ? await studioApi.get(id) : null);
                setError("");
            } catch (cause) {
                setError(cause instanceof Error ? cause.message : "无法连接本地服务");
            }
        })();
    }, [open, canvasId]);

    const act = async (action: () => Promise<unknown>) => {
        if (!project) return;
        setBusy(true);
        try {
            await action();
            setProject(await studioApi.get(project.id));
            setError("");
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : "操作失败");
        } finally {
            setBusy(false);
        }
    };

    const selected = nodes.find((node) => node.id === selectedNodeId);
    const inputs = connections.filter((connection) => connection.toNodeId === selectedNodeId).map((connection) => nodes.find((node) => node.id === connection.fromNodeId)).filter((node): node is CanvasNodeData => Boolean(node));
    const confirmed = project?.facts.filter((fact) => fact.status === "已知事实") || [];
    const masterRequest: MasterRequest = { group: candidateGroup, view_label: viewLabel, views: viewPreset, source_id: candidateSourceId || "" };
    const candidates = project?.sources.filter((source) => source.origin === "SeeAny candidate") || [];

    return (
        <Drawer title="广告电商工作台" open={open} onClose={onClose} width={460} styles={{ body: { overflowY: "auto" } }}>
            <Space direction="vertical" size="large" className="w-full">
                {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
                {health ? <Space wrap><Tag color={health.deepseek ? "success" : "default"}>DeepSeek {health.deepseek ? "已配置" : "未配置"}</Tag><Tag color={health.seeany ? "success" : "default"}>SeeAny {health.seeany ? "已配置" : "未配置"}</Tag><Tag color={health.flova ? "success" : "default"}>Flova {health.flova ? "可用" : "未找到"}</Tag></Space> : null}
                {!project ? (
                    <section>
                        <Typography.Paragraph>此画布尚未关联本地商品项目。原始资料将保存在本机，不进入浏览器画布备份。</Typography.Paragraph>
                        <Button type="primary" disabled={busy || Boolean(error)} onClick={() => void (async () => {
                            setBusy(true);
                            try {
                                const created = await studioApi.create(title);
                                await localforage.setItem(`commerce-studio:${canvasId}`, created.id);
                                setProject(created);
                            } catch (cause) { setError(cause instanceof Error ? cause.message : "创建失败"); }
                            finally { setBusy(false); }
                        })()}>创建商品项目</Button>
                    </section>
                ) : (
                    <>
                        <section>
                            <Typography.Title level={5}>原始资料</Typography.Title>
                            <input type="file" accept="image/*,text/plain,application/pdf" disabled={busy} onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) void act(() => studioApi.source(project.id, file));
                                event.target.value = "";
                            }} />
                            {project.sources.map((source) => <div key={source.id}><Typography.Text>{source.name}</Typography.Text> <Typography.Text type="secondary">{Math.round(source.bytes / 1024)} KB</Typography.Text></div>)}
                        </section>
                        <section>
                            <Typography.Title level={5}>产品事实</Typography.Title>
                            <Space direction="vertical" className="w-full">
                                <Input placeholder="字段，例如颜色、容量、适用场景" value={field} onChange={(event) => setField(event.target.value)} />
                                <Input.TextArea placeholder="依据资料填写；推断内容保持待核实" value={value} onChange={(event) => setValue(event.target.value)} />
                                <Select placeholder="依据来源" allowClear value={sourceId} onChange={setSourceId} options={project.sources.map((source) => ({ value: source.id, label: source.name }))} />
                                <Select value={factStatus} onChange={setFactStatus} options={["待核实", "已知事实", "创意假设"].map((status) => ({ value: status, label: status }))} />
                                <Button disabled={busy || !field.trim() || !value.trim()} onClick={() => void act(async () => {
                                    await studioApi.fact(project.id, { field, value, source_id: sourceId, status: factStatus });
                                    setField(""); setValue("");
                                })}>保存事实</Button>
                            </Space>
                            {project.facts.map((fact) => <div key={fact.id} className="mt-2"><Tag>{fact.status}</Tag>{fact.field}：{fact.value}</div>)}
                            <Button className="mt-3" disabled={busy || !confirmed.length} onClick={() => void act(() => studioApi.brief(project.id, confirmed.map((fact) => fact.id)))}>确认简报新版本</Button>
                            <Typography.Text type="secondary" className="ml-2">已确认 {project.brief_versions.length} 版</Typography.Text>
                        </section>
                        <section>
                            <Typography.Title level={5}>白底三视图候选</Typography.Title>
                            <Typography.Paragraph type="secondary">每组分别提交正面、侧面、背面任务；最多两组。SeeAny 视角预设请按当前账号文档填写。</Typography.Paragraph>
                            <Space direction="vertical" className="w-full">
                                <Select value={candidateGroup} onChange={(next) => { setCandidateGroup(next); setMasterQuote(null); }} options={[1, 2].map((group) => ({ value: group, label: `第 ${group} 组` }))} />
                                <Select value={viewLabel} onChange={(next) => { setViewLabel(next); setViewPreset(next === "正面" ? "front" : ""); setMasterQuote(null); }} options={["正面", "侧面", "背面"].map((view) => ({ value: view, label: view }))} />
                                <Input placeholder="SeeAny views 预设" value={viewPreset} onChange={(event) => { setViewPreset(event.target.value); setMasterQuote(null); }} />
                                <Select placeholder="选择原始实拍" value={candidateSourceId} onChange={(next) => { setCandidateSourceId(next); setMasterQuote(null); }} options={project.sources.filter((source) => source.mime.startsWith("image/") && !source.origin).map((source) => ({ value: source.id, label: source.name }))} />
                                <Button disabled={busy || !project.brief_versions.length || !candidateSourceId || !viewPreset.trim()} onClick={() => void act(async () => { setMasterQuote(await studioApi.masterQuote(project.id, masterRequest)); })}>查看本次输入与费用</Button>
                                {masterQuote ? <><Typography.Paragraph>输入版本：{String((masterQuote.input_snapshot as { brief_id?: string }).brief_id || "—").slice(0, 12)}；预估费用：{masterQuote.estimate == null ? "未知" : masterQuote.estimate}；计价来源：{masterQuote.pricing_source}</Typography.Paragraph><Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.masterRun(project.id, { ...masterRequest, approved_fingerprint: masterQuote.fingerprint, request_id: crypto.randomUUID() }); setMasterQuote(null); })}>确认提交 SeeAny</Button></> : null}
                            </Space>
                            {project.tasks.filter((task) => task.kind === "master").map((task) => <div key={task.id} className="mt-2"><Tag>{task.status}</Tag>第 {task.candidate_group} 组 · {task.view_label} <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.sync(project.id, task.id))}>同步状态</Button></div>)}
                        </section>
                        <section>
                            <Typography.Title level={5}>三视图母版审核</Typography.Title>
                            <Typography.Paragraph type="secondary">仅确认整体创作参考；缺失视角的结构细节仍需单独核实。</Typography.Paragraph>
                            <Select mode="multiple" className="w-full" placeholder="选择同组正面、侧面、背面各一张" value={masterIds} onChange={setMasterIds} options={candidates.map((source) => ({ value: source.id, label: `第 ${source.candidate_group} 组 · ${source.view_label} · ${source.name}` }))} />
                            <Button className="mt-2" disabled={busy || masterIds.length !== 3} onClick={() => void act(() => studioApi.master(project.id, masterIds))}>确认母版新版本</Button>
                            <Typography.Text type="secondary" className="ml-2">已确认 {project.master_versions.length} 版</Typography.Text>
                            {project.master_versions.at(-1)?.asset_ids.map((id) => { const source = project.sources.find((item) => item.id === id); return source ? <div key={id}>{source.view_label} · {source.name} <Button type="link" disabled={busy} onClick={() => void (async () => { try { const asset = await studioApi.sourceData(project.id, id); await onInsertImage(asset.data_url, `母版${source.view_label}`); } catch (cause) { setError(cause instanceof Error ? cause.message : "加入画布失败"); } })()}>加入画布</Button></div> : null; })}
                        </section>
                        <section>
                            <Typography.Title level={5}>创作聊天</Typography.Title>
                            <Typography.Paragraph type="secondary">DeepSeek 仅读取本次需求和最新已确认简报；讨论内容不会自动进入正式生成。</Typography.Paragraph>
                            <Input.TextArea rows={3} value={chatPrompt} onChange={(event) => { setChatPrompt(event.target.value); setChatQuote(null); }} placeholder="描述广告目标、疑问或修改建议" />
                            <Space className="mt-2">
                                <Button disabled={busy || !chatPrompt.trim()} onClick={() => void act(async () => { setChatQuote(await studioApi.chatQuote(project.id, chatPrompt)); })}>查看输入与费用</Button>
                                {chatQuote ? <Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.chatRun(project.id, chatPrompt, chatQuote.fingerprint, crypto.randomUUID()); setChatPrompt(""); setChatQuote(null); })}>确认发送 DeepSeek</Button> : null}
                            </Space>
                            {chatQuote ? <Typography.Paragraph className="mt-2">费用：{chatQuote.estimate == null ? "未知" : chatQuote.estimate}；简报版本：{String((chatQuote.input_snapshot as { brief_id?: string }).brief_id || "无").slice(0, 12)}</Typography.Paragraph> : null}
                            {project.chat.map((item) => <div key={item.id} className="mt-3"><Typography.Text strong>{item.prompt}</Typography.Text><Typography.Paragraph className="mt-1 whitespace-pre-wrap">{item.reply}</Typography.Paragraph></div>)}
                        </section>
                        <section>
                            <Typography.Title level={5}>画布输入</Typography.Title>
                            <Typography.Paragraph>{selected ? `当前节点：${selected.title || selected.type}` : "选择一个画布节点查看其输入依赖"}</Typography.Paragraph>
                            {inputs.length ? inputs.map((node) => <Tag key={node.id}>{node.type} · {node.title || node.id}</Tag>) : <Typography.Text type="secondary">等待连接</Typography.Text>}
                            <Typography.Paragraph type="secondary" className="mt-2">连线仅编辑画布。任务提交前需查看实际输入和费用；未知费用不会自动提交。</Typography.Paragraph>
                            {selected?.type === CanvasNodeType.Config ? <Space direction="vertical" className="mt-2 w-full">
                                <Select value={previewRatio} onChange={setPreviewRatio} options={["1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"].map((ratio) => ({ value: ratio, label: ratio }))} />
                                <Button disabled={busy || !project.master_versions.length} onClick={() => void act(async () => {
                                    const context = await hydrateNodeGenerationContext(buildNodeGenerationContext(selected.id, nodes, connections, selected.metadata?.composerContent || selected.metadata?.prompt || ""));
                                    if (context.referenceImages.length !== 1 || context.referenceVideos.length || context.referenceAudios.length) throw new Error("试图需要恰好一张连线参考图，暂不支持视频或音频输入");
                                    const request: PreviewRequest = { canvas_project_id: canvasId, config_node_id: selected.id, reference_node_id: context.referenceImages[0].id,
                                        connection_ids: connections.filter((edge) => edge.toNodeId === selected.id).map((edge) => edge.id),
                                        prompt: context.prompt, ratio: previewRatio, reference_data_url: context.referenceImages[0].dataUrl };
                                    setPreviewRequest(request);
                                    setPreviewQuote(await studioApi.previewQuote(project.id, request));
                                })}>按当前连线查看试图输入</Button>
                                {previewQuote && previewRequest ? <><Typography.Paragraph className="whitespace-pre-wrap">提示词：{previewRequest.prompt}</Typography.Paragraph><Typography.Paragraph>参考图节点：{previewRequest.reference_node_id}；费用：{previewQuote.estimate == null ? "未知" : previewQuote.estimate}</Typography.Paragraph><Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.previewRun(project.id, { ...previewRequest, approved_fingerprint: previewQuote.fingerprint, request_id: crypto.randomUUID() }); setPreviewQuote(null); setPreviewRequest(null); })}>确认提交 SeeAny 单张试图</Button></> : null}
                            </Space> : null}
                        </section>
                        <section>
                            <Typography.Title level={5}>任务与费用</Typography.Title>
                            <Typography.Paragraph>任务 {project.tasks.length} 个；未核实价格显示“未知”。</Typography.Paragraph>
                            {project.tasks.filter((task) => task.kind === "preview").map((task) => <div key={task.id} className="mt-2"><Tag>{task.status}</Tag>画布试图 <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.sync(project.id, task.id))}>同步状态</Button>{task.asset_ids?.length && task.source_id ? <Button type="link" onClick={() => void (async () => { try { const [original, generated] = await Promise.all([studioApi.sourceData(project.id, task.source_id!), studioApi.sourceData(project.id, task.asset_ids![0])]); setComparison({ original: original.data_url, generated: generated.data_url }); } catch (cause) { setError(cause instanceof Error ? cause.message : "读取图片失败"); } })()}>对照查看</Button> : null}</div>)}
                            {comparison ? <div className="mt-3 grid grid-cols-2 gap-2"><div><Typography.Text>原始参考</Typography.Text><img src={comparison.original} alt="原始参考" className="w-full" /></div><div><Typography.Text>生成候选</Typography.Text><img src={comparison.generated} alt="生成候选" className="w-full" /></div></div> : null}
                        </section>
                    </>
                )}
            </Space>
        </Drawer>
    );
}
