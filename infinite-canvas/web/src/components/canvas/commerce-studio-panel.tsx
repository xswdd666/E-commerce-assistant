import { useEffect, useState } from "react";
import { Button, Drawer, Input, Select, Space, Tag, Typography } from "antd";
import localforage from "localforage";
import { saveAs } from "file-saver";

import { studioApi, type MasterRequest, type PreviewRequest, type StudioFact, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";
import { buildNodeGenerationContext, hydrateNodeGenerationContext } from "@/components/canvas/canvas-node-generation";
import { CanvasNodeType } from "@/types/canvas";
import { CommercePlanSection } from "@/components/canvas/commerce-plan-section";
import { CommerceFlovaSection } from "@/components/canvas/commerce-flova-section";
import { CommerceGallerySection } from "@/components/canvas/commerce-gallery-section";
import { CommercePromptSection } from "@/components/canvas/commerce-prompt-section";
import { CommerceFinishingSection } from "@/components/canvas/commerce-finishing-section";
import { createCanvasExportBlob } from "@/lib/canvas/canvas-export";
import { createZip } from "@/lib/zip";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";

type Props = { open: boolean; onClose: () => void; canvasId: string; title: string; nodes: CanvasNodeData[]; connections: CanvasConnection[]; selectedNodeId?: string; onInsertImage: (dataUrl: string, title: string) => Promise<void>; onInsertText: (text: string, title: string) => void };

export function CommerceStudioPanel({ open, onClose, canvasId, title, nodes, connections, selectedNodeId, onInsertImage, onInsertText }: Props) {
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
    const [factsOffer, setFactsOffer] = useState<{ sourceId: string; quote: StudioQuote } | null>(null);

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
    const confirmedCosts = Object.entries((project?.costs || []).reduce<Record<string, number>>((totals, entry) => {
        if (entry.actual != null && entry.currency) totals[entry.currency] = (totals[entry.currency] || 0) + entry.actual;
        return totals;
    }, {}));
    const pendingCostCount = project?.costs.filter((entry) => entry.actual == null).length || 0;
    const exportWholeProject = async () => {
        if (!project) return;
        const canvas = useCanvasStore.getState().openProject(canvasId);
        if (!canvas) throw new Error("无法读取当前画布项目");
        const [canvasZip, commerceZip] = await Promise.all([createCanvasExportBlob([canvas]), studioApi.backupBlob(project.id)]);
        const bundle = await createZip([
            { name: "bundle.json", data: JSON.stringify({ format: "commerce-studio-bundle", version: 1, canvas_id: canvasId, commerce_id: project.id }) },
            { name: "canvas.zip", data: canvasZip },
            { name: "commerce.zip", data: commerceZip },
        ]);
        saveAs(bundle, `${project.name.replace(/[\\/:*?"<>|]/g, "_")}-完整备份.zip`);
    };

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
                            <input type="file" accept="image/*,text/plain,application/pdf,.docx" disabled={busy} onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) void act(() => studioApi.source(project.id, file));
                                event.target.value = "";
                            }} />
                            {project.sources.filter((source) => !source.origin).map((source) => <div key={source.id}><Typography.Text>{source.name}</Typography.Text> <Typography.Text type="secondary">{Math.round(source.bytes / 1024)} KB · {source.parse_status || "需人工查看"}</Typography.Text>{source.parse_status === "可提取文本" ? <Button type="link" disabled={busy} onClick={() => void act(async () => { setFactsOffer({ sourceId: source.id, quote: await studioApi.factsQuote(project.id, source.id) }); })}>提取候选事实</Button> : null}</div>)}
                            {factsOffer ? <div><Typography.Text>DeepSeek 提取费用：{factsOffer.quote.estimate == null ? "未知" : factsOffer.quote.estimate}</Typography.Text><Button type="primary" className="ml-2" disabled={busy} onClick={() => void act(async () => { await studioApi.factsRun(project.id, factsOffer.sourceId, factsOffer.quote.fingerprint, crypto.randomUUID()); setFactsOffer(null); })}>确认提取</Button></div> : null}
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
                            {project.facts.map((fact) => <div key={fact.id} className="mt-2"><Tag>{fact.status}</Tag>{fact.field}：{fact.value}{fact.status === "待核实" ? <Space><Button type="link" disabled={busy} onClick={() => void act(() => studioApi.reviewFact(project.id, fact.id, "已知事实"))}>核实为事实</Button><Button type="link" disabled={busy} onClick={() => void act(() => studioApi.reviewFact(project.id, fact.id, "创意假设"))}>标为假设</Button></Space> : null}</div>)}
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
                            {candidates.map((source) => <div key={source.id}>第 {source.candidate_group} 组 · {source.view_label} <Button type="link" onClick={() => void (async () => { try { const [original, generated] = await Promise.all([studioApi.sourceData(project.id, source.reference_ids?.[0] || ""), studioApi.sourceData(project.id, source.id)]); setComparison({ original: original.data_url, generated: generated.data_url }); } catch (cause) { setError(cause instanceof Error ? cause.message : "读取候选失败"); } })()}>对照查看</Button></div>)}
                            <Button className="mt-2" disabled={busy || masterIds.length !== 3} onClick={() => void act(() => studioApi.master(project.id, masterIds))}>确认母版新版本</Button>
                            <Typography.Text type="secondary" className="ml-2">已确认 {project.master_versions.length} 版</Typography.Text>
                            {project.master_versions.at(-1)?.asset_ids.map((id) => { const source = project.sources.find((item) => item.id === id); return source ? <div key={id}>{source.view_label} · {source.name} <Button type="link" disabled={busy} onClick={() => void (async () => { try { const asset = await studioApi.sourceData(project.id, id); await onInsertImage(asset.data_url, `母版${source.view_label}`); } catch (cause) { setError(cause instanceof Error ? cause.message : "加入画布失败"); } })()}>加入画布</Button></div> : null; })}
                            {comparison ? <div className="mt-3 grid grid-cols-2 gap-2"><div><Typography.Text>参考图</Typography.Text><img src={comparison.original} alt="参考图" className="w-full" /></div><div><Typography.Text>候选图</Typography.Text><img src={comparison.generated} alt="候选图" className="w-full" /></div></div> : null}
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
                        <CommercePlanSection project={project} busy={busy} act={act} />
                        <CommercePromptSection project={project} busy={busy} act={act} onInsertImage={onInsertImage} onInsertText={onInsertText} />
                        <CommerceGallerySection project={project} busy={busy} act={act} />
                        <CommerceFlovaSection project={project} busy={busy} act={act} />
                        <CommerceFinishingSection project={project} busy={busy} act={act} />
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
                            <Typography.Paragraph>任务 {project.tasks.length} 个；待结算 {pendingCostCount} 个。{confirmedCosts.length ? `已确认实付：${confirmedCosts.map(([unit, amount]) => `${amount.toFixed(2)} ${unit}`).join("、")}` : "尚无供应商返回的可信实付金额"}。预估与实付分开记录；未知价格不阻止手动提交。</Typography.Paragraph>
                            {project.costs.map((entry) => <div key={entry.id}><Tag>{entry.provider}</Tag>{entry.stage} · {entry.purpose} · {entry.status} · 实付 {entry.actual == null ? "未知" : `${entry.actual} ${entry.currency || "单位待核实"}`}</div>)}
                            {project.tasks.filter((task) => task.kind === "preview").map((task) => <div key={task.id} className="mt-2"><Tag>{task.status}</Tag>画布试图 <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.sync(project.id, task.id))}>同步状态</Button>{task.asset_ids?.length && task.source_id ? <Button type="link" onClick={() => void (async () => { try { const [original, generated] = await Promise.all([studioApi.sourceData(project.id, task.source_id!), studioApi.sourceData(project.id, task.asset_ids![0])]); setComparison({ original: original.data_url, generated: generated.data_url }); } catch (cause) { setError(cause instanceof Error ? cause.message : "读取图片失败"); } })()}>对照查看</Button> : null}</div>)}
                        </section>
                        <section><Typography.Title level={5}>项目备份</Typography.Title><Typography.Paragraph type="secondary">将当前画布、浏览器媒体文件、本地原图、版本和任务记录一起打包。恢复入口位于画布列表的“导入”。</Typography.Paragraph><Button disabled={busy} onClick={() => void act(exportWholeProject)}>下载完整项目备份</Button></section>
                    </>
                )}
            </Space>
        </Drawer>
    );
}
