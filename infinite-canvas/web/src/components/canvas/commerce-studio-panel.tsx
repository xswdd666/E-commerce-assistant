import { useEffect, useState } from "react";
import { Button, Checkbox, Drawer, Input, Select, Space, Tag, Typography } from "antd";
import localforage from "localforage";
import { saveAs } from "file-saver";

import { studioApi, type FlovaCanvasContext, type MasterRequest, type PreviewRequest, type StudioFact, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";
import { buildNodeGenerationContext, hydrateNodeGenerationContext } from "@/components/canvas/canvas-node-generation";
import { CanvasNodeType } from "@/types/canvas";
import { CommercePlanSection } from "@/components/canvas/commerce-plan-section";
import { CommerceFlovaSection } from "@/components/canvas/commerce-flova-section";
import { CommerceGallerySection } from "@/components/canvas/commerce-gallery-section";
import { CommercePromptSection } from "@/components/canvas/commerce-prompt-section";
import { CommerceFinishingSection } from "@/components/canvas/commerce-finishing-section";
import { CommerceDeliverySection } from "@/components/canvas/commerce-delivery-section";
import { CommerceCostSection } from "@/components/canvas/commerce-cost-section";
import { CommerceObservationSection } from "@/components/canvas/commerce-observation-section";
import { createCanvasExportBlob } from "@/lib/canvas/canvas-export";
import { createZip } from "@/lib/zip";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { commerceNodeStatus, previewInputChanged } from "@/lib/canvas/commerce-node-status";
import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";

type Props = { open: boolean; onClose: () => void; canvasId: string; title: string; nodes: CanvasNodeData[]; connections: CanvasConnection[]; selectedNodeId?: string; staleNodeIds: ReadonlySet<string>; onProjectChange: (project: StudioProject) => void; onInsertImage: (dataUrl: string, title: string, commerceImage?: { projectId: string; sourceId: string; masterVersionId: string }, commercePromptImage?: { projectId: string; sourceId: string; versionId: string }, commercePreview?: { projectId: string; taskId: string; sourceId: string; configNodeId: string }) => Promise<void>; onInsertText: (text: string, title: string, source?: { projectId: string; kind: "script" | "storyboard" | "prompt"; versionId: string; parentVersionId?: string | null }) => void; onInsertVideo: (blob: Blob, title: string, projectId: string, deliverableId: string) => Promise<void> };

export function CommerceStudioPanel({ open, onClose, canvasId, title, nodes, connections, selectedNodeId, staleNodeIds, onProjectChange, onInsertImage, onInsertText, onInsertVideo }: Props) {
    const [project, setProject] = useState<StudioProject | null>(null);
    const [legacyProjects, setLegacyProjects] = useState<Array<{ id: string; name: string; sources: number; facts: number }>>([]);
    const [legacyId, setLegacyId] = useState<string>();
    const [health, setHealth] = useState<{ deepseek: boolean; seeany: boolean; flova: boolean; jev: boolean } | null>(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [field, setField] = useState("");
    const [value, setValue] = useState("");
    const [sourceId, setSourceId] = useState<string>();
    const [factStatus, setFactStatus] = useState<StudioFact["status"]>("待核实");
    const [masterIds, setMasterIds] = useState<string[]>([]);
    const [inferredText, setInferredText] = useState("");
    const [detailEvidence, setDetailEvidence] = useState<Record<string, string>>({});
    const [evidencePreview, setEvidencePreview] = useState<{ id: string; dataUrl: string } | null>(null);
    const [candidateGroup, setCandidateGroup] = useState(1);
    const [viewLabel, setViewLabel] = useState("正面");
    const [viewPreset, setViewPreset] = useState("front");
    const [candidateSourceId, setCandidateSourceId] = useState<string>();
    const [masterQuote, setMasterQuote] = useState<StudioQuote | null>(null);
    const [chatPrompt, setChatPrompt] = useState("");
    const [chatQuote, setChatQuote] = useState<StudioQuote | null>(null);
    const [previewQuote, setPreviewQuote] = useState<StudioQuote | null>(null);
    const [previewRequest, setPreviewRequest] = useState<PreviewRequest | null>(null);
    const [canvasFlovaOffer, setCanvasFlovaOffer] = useState<StudioQuote | null>(null);
    const [canvasFlovaRequest, setCanvasFlovaRequest] = useState<FlovaCanvasContext | null>(null);
    const [canvasFlovaShotId, setCanvasFlovaShotId] = useState<string | null>(null);
    const [canvasAutoRetry, setCanvasAutoRetry] = useState(false);
    const [previewRatio, setPreviewRatio] = useState("1:1");
    const [comparison, setComparison] = useState<{ original: string; generated: string } | null>(null);
    const [previewComparison, setPreviewComparison] = useState<{ taskId: string; original: string; generated: string } | null>(null);
    const [factsOffer, setFactsOffer] = useState<{ sourceId: string; quote: StudioQuote } | null>(null);
    const [previewSourceId, setPreviewSourceId] = useState<string | null>(null);
    const [resolvingTaskId, setResolvingTaskId] = useState<string | null>(null);
    const [resolutionNote, setResolutionNote] = useState("");

    useEffect(() => { setPreviewQuote(null); setPreviewRequest(null); setCanvasFlovaOffer(null); setCanvasFlovaRequest(null); }, [nodes, connections, selectedNodeId, previewRatio]);
    useEffect(() => { setCanvasFlovaShotId(null); setCanvasFlovaOffer(null); setCanvasFlovaRequest(null); }, [project?.storyboard_approval]);
    useEffect(() => { if (project) onProjectChange(project); }, [project, onProjectChange]);

    useEffect(() => {
        if (!open) return;
        void (async () => {
            try {
                setHealth(await studioApi.health());
                const id = await localforage.getItem<string>(`commerce-studio:${canvasId}`);
                setProject(id ? await studioApi.get(id) : null);
                if (!id) setLegacyProjects(await studioApi.legacyProjects());
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

    const uploadSources = async (files: FileList) => {
        if (!project || busy || !files.length) return;
        setBusy(true);
        const failures: string[] = [];
        for (const file of Array.from(files)) {
            try { await studioApi.source(project.id, file); }
            catch (cause) { failures.push(`${file.name}：${cause instanceof Error ? cause.message : "上传失败"}`); }
        }
        try { setProject(await studioApi.get(project.id)); }
        catch (cause) { failures.push(cause instanceof Error ? cause.message : "刷新项目失败"); }
        setError(failures.join("；"));
        setBusy(false);
    };

    const selected = nodes.find((node) => node.id === selectedNodeId);
    const selectedSource = selected?.metadata?.commerceSource;
    const selectedAsset = selected?.metadata?.commerceAsset;
    const selectedImage = selected?.metadata?.commerceImage;
    const selectedPromptImage = selected?.metadata?.commercePromptImage;
    const selectedPreview = selected?.metadata?.commercePreview;
    const selectedImageSource = project?.sources.find((item) => item.id === selectedImage?.sourceId);
    const selectedSourceCurrent = Boolean(project && selectedSource && selectedSource.projectId === project.id && selectedSource.originalText === selected?.metadata?.content &&
        (selectedSource.kind === "script" ? selectedSource.versionId === project.script_approval : selectedSource.kind === "storyboard" ? selectedSource.versionId === project.storyboard_approval :
            project.prompt_versions.some((version) => version.id === selectedSource.versionId && version.text === selectedSource.originalText && version.brief_id === project.brief_versions.at(-1)?.id && version.master_id === project.master_versions.at(-1)?.id)));
    const inputs = connections.filter((connection) => connection.toNodeId === selectedNodeId).map((connection) => nodes.find((node) => node.id === connection.fromNodeId)).filter((node): node is CanvasNodeData => Boolean(node));
    const nodeNames = Object.fromEntries(nodes.map((node) => [node.id, node.title || node.type]));
    const selectedCosts = project?.costs.filter((cost) => selectedNodeId && cost.node_id === selectedNodeId) || [];
    const selectedSpend = new Map<string, number>();
    for (const cost of selectedCosts) if (cost.actual != null && cost.currency) selectedSpend.set(cost.currency, (selectedSpend.get(cost.currency) || 0) + cost.actual);
    const confirmed = project?.facts.filter((fact) => fact.status === "已知事实") || [];
    const currentMaster = project?.master_versions.at(-1);
    const currentStoryboard = project?.storyboard_versions.find((item) => item.id === project.storyboard_approval);
    const canvasShotIndex = currentStoryboard?.shots.findIndex((shot) => shot.id === canvasFlovaShotId) ?? -1;
    const canvasShotReady = !canvasFlovaShotId || Boolean(currentStoryboard && canvasShotIndex >= 0 && !project?.shot_approvals[canvasFlovaShotId]
        && currentStoryboard.shots.slice(0, canvasShotIndex).every((shot) => project?.shot_approvals[shot.id]?.storyboard_id === currentStoryboard.id));
    const canvasRemainingRetries = Math.max(0, 2 - (project?.tasks.filter((task) => task.kind === "video_shot" && task.automatic_retry && (task.input_snapshot?.shot_id === canvasFlovaShotId || task.node_id === selectedNodeId)).length || 0));
    const masterRequest: MasterRequest = { group: candidateGroup, view_label: viewLabel, views: viewPreset, source_id: candidateSourceId || "" };
    const candidates = project?.sources.filter((source) => source.origin === "SeeAny candidate") || [];
    const downloadSource = async (sourceId: string) => {
        if (!project) return;
        const source = await studioApi.sourceData(project.id, sourceId);
        const link = document.createElement("a");
        link.href = source.data_url;
        link.download = source.name.replace(/[\\/:*?"<>|]/g, "_");
        link.click();
    };
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
                {health ? <Space wrap><Tag color={health.deepseek ? "success" : "default"}>DeepSeek {health.deepseek ? "已配置" : "未配置"}</Tag><Tag color={health.seeany ? "success" : "default"}>SeeAny {health.seeany ? "已配置" : "未配置"}</Tag><Tag color={health.flova ? "success" : "default"}>Flova {health.flova ? "可用" : "未找到"}</Tag><Tag color={health.jev ? "success" : "default"}>Jev {health.jev ? "已配置" : "未配置"}</Tag></Space> : null}
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
                        {legacyProjects.length ? <div className="mt-4">
                            <Typography.Paragraph type="secondary">旧原型项目可只读复制资料与事实；旧版母版和成品保存在历史快照中，须重新审核后才能用于新任务。</Typography.Paragraph>
                            <Space direction="vertical" className="w-full"><Select className="w-full" placeholder="选择旧项目" value={legacyId} onChange={setLegacyId} options={legacyProjects.map((item) => ({ value: item.id, label: `${item.name} · ${item.sources} 份资料 · ${item.facts} 条事实` }))} />
                                <Button disabled={busy || !legacyId} onClick={() => void (async () => {
                                    setBusy(true);
                                    try {
                                        const imported = await studioApi.legacyImport(legacyId!);
                                        await localforage.setItem(`commerce-studio:${canvasId}`, imported.id);
                                        setProject(imported);
                                        setError("");
                                    } catch (cause) { setError(cause instanceof Error ? cause.message : "导入失败"); }
                                    finally { setBusy(false); }
                                })()}>导入旧原型项目</Button></Space>
                        </div> : null}
                    </section>
                ) : (
                    <>
                        {project.legacy_import ? <section><Typography.Paragraph type="warning">已从旧原型导入。{project.legacy_import.review_required}；旧预算设置不会阻止新任务。</Typography.Paragraph><Button onClick={() => saveAs(new Blob([JSON.stringify(project.legacy_import?.archive, null, 2)], { type: "application/json" }), `${project.name}-旧原型历史.json`)}>下载旧项目历史快照</Button></section> : null}
                        <section>
                            <Typography.Title level={5}>原始资料</Typography.Title>
                            <div className="rounded-lg border border-dashed p-3" onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); }} onDrop={(event) => { event.preventDefault(); event.stopPropagation(); void uploadSources(event.dataTransfer.files); }}>
                                <Typography.Paragraph type="secondary">拖入商品实拍、截图、TXT、PDF 或 DOCX，也可选择多个文件。</Typography.Paragraph>
                                <input type="file" multiple accept="image/*,text/plain,application/pdf,.docx" disabled={busy} onChange={(event) => { if (event.target.files) void uploadSources(event.target.files); event.target.value = ""; }} />
                            </div>
                            {project.sources.filter((source) => !source.origin).map((source) => <div key={source.id} className="mt-2"><Typography.Text>{source.name}</Typography.Text> <Typography.Text type="secondary">{Math.round(source.bytes / 1024)} KB · {source.parse_status || "需人工查看"}</Typography.Text><Button type="link" disabled={busy} onClick={() => void act(() => downloadSource(source.id))}>下载原始文件</Button>{source.extracted_text ? <Button type="link" onClick={() => setPreviewSourceId((current) => current === source.id ? null : source.id)}>{previewSourceId === source.id ? "收起正文" : "查看提取正文"}</Button> : null}{source.parse_status === "可提取文本" || ["image/jpeg", "image/png", "image/gif", "image/webp"].includes(source.mime) ? <Button type="link" disabled={busy} onClick={() => void act(async () => { setFactsOffer({ sourceId: source.id, quote: await studioApi.factsQuote(project.id, source.id) }); })}>{source.mime.startsWith("image/") ? "识图提取候选事实" : "提取候选事实"}</Button> : null}{previewSourceId === source.id ? <Typography.Paragraph className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap">{source.extracted_text}</Typography.Paragraph> : null}</div>)}
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
                            {project.tasks.filter((task) => task.kind === "master").map((task) => <div key={task.id} className="mt-2"><Tag>{task.status}</Tag>第 {task.candidate_group} 组 · {task.view_label} <Button type="link" disabled={busy || !task.remote_id} onClick={() => void act(() => studioApi.sync(project.id, task.id))}>同步状态</Button>{task.error ? <Typography.Text type="danger">{task.error}</Typography.Text> : null}</div>)}
                        </section>
                        <section>
                            <Typography.Title level={5}>三视图母版审核</Typography.Title>
                            <Typography.Paragraph type="secondary">仅确认整体创作参考；缺失视角的结构细节仍需单独核实。</Typography.Paragraph>
                            <Select mode="multiple" className="w-full" placeholder="选择同组正面、侧面、背面各一张" value={masterIds} onChange={setMasterIds} options={candidates.map((source) => ({ value: source.id, label: `第 ${source.candidate_group} 组 · ${source.view_label} · ${source.name}` }))} />
                            <Input.TextArea className="mt-2" rows={3} placeholder="逐行填写缺少原图证据的结构细节，例如背面接口位置；没有则留空" value={inferredText} onChange={(event) => setInferredText(event.target.value)} />
                            {candidates.map((source) => <div key={source.id}>第 {source.candidate_group} 组 · {source.view_label} <Button type="link" onClick={() => void (async () => { try { const [original, generated] = await Promise.all([studioApi.sourceData(project.id, source.reference_ids?.[0] || ""), studioApi.sourceData(project.id, source.id)]); setComparison({ original: original.data_url, generated: generated.data_url }); } catch (cause) { setError(cause instanceof Error ? cause.message : "读取候选失败"); } })()}>对照查看</Button></div>)}
                            <Button className="mt-2" disabled={busy || masterIds.length !== 3} onClick={() => void act(() => studioApi.master(project.id, masterIds, inferredText.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)))}>确认母版新版本</Button>
                            <Typography.Text type="secondary" className="ml-2">已确认 {project.master_versions.length} 版</Typography.Text>
                            {currentMaster?.asset_ids.map((id) => { const source = project.sources.find((item) => item.id === id); return source ? <div key={id}>{source.view_label} · {source.name} <Button type="link" disabled={busy} onClick={() => void (async () => { try { const asset = await studioApi.sourceData(project.id, id); await onInsertImage(asset.data_url, `母版${source.view_label}`, { projectId: project.id, sourceId: id, masterVersionId: currentMaster.id }); } catch (cause) { setError(cause instanceof Error ? cause.message : "加入画布失败"); } })()}>加入画布</Button></div> : null; })}
                            {project.master_versions.at(-1)?.inferred_details.map((detail, index) => typeof detail === "string" ? <div key={index}><Tag color="warning">历史待核实</Tag>{detail}</div> : <div key={detail.id} className="mt-2"><Tag color={detail.status === "已核实" ? "success" : "warning"}>{detail.status}</Tag>{detail.text}{detail.evidence_source_id ? ` · 依据：${project.sources.find((source) => source.id === detail.evidence_source_id)?.name || detail.evidence_source_id}` : null}{detail.status !== "已核实" ? <Space className="mt-1"><Select className="min-w-36" placeholder="原始实拍证据" value={detailEvidence[detail.id]} onChange={(id) => { setDetailEvidence((current) => ({ ...current, [detail.id]: id })); setEvidencePreview(null); }} options={project.sources.filter((source) => source.mime.startsWith("image/") && !source.origin).map((source) => ({ value: source.id, label: source.name }))} /><Button disabled={busy || !detailEvidence[detail.id]} onClick={() => void (async () => { try { const result = await studioApi.sourceData(project.id, detailEvidence[detail.id]); setEvidencePreview({ id: detailEvidence[detail.id], dataUrl: result.data_url }); } catch (cause) { setError(cause instanceof Error ? cause.message : "读取原图失败"); } })()}>查看原图</Button><Button disabled={busy || !detailEvidence[detail.id] || evidencePreview?.id !== detailEvidence[detail.id]} onClick={() => void act(() => studioApi.verifyMasterDetail(project.id, detail.id, detailEvidence[detail.id]))}>逐项核实并创建母版新版</Button></Space> : null}</div>)}
                            {evidencePreview ? <img src={evidencePreview.dataUrl} alt="待核实结构的原始实拍" className="mt-2 w-full" /> : null}
                            {comparison ? <div className="mt-3 grid grid-cols-2 gap-2"><div><Typography.Text>参考图</Typography.Text><img src={comparison.original} alt="参考图" className="w-full" /></div><div><Typography.Text>候选图</Typography.Text><img src={comparison.generated} alt="候选图" className="w-full" /></div></div> : null}
                        </section>
                        <CommerceObservationSection project={project} busy={busy} act={act} />
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
                        <CommercePlanSection project={project} busy={busy} act={act} onInsertText={onInsertText} />
                        <CommercePromptSection project={project} busy={busy} act={act} onInsertImage={onInsertImage} onInsertText={onInsertText} />
                        <CommerceGallerySection project={project} busy={busy} act={act} />
                        <CommerceFlovaSection project={project} busy={busy} act={act} onInsertVideo={onInsertVideo} />
                        <CommerceFinishingSection project={project} busy={busy} act={act} />
                        <CommerceDeliverySection project={project} busy={busy} act={act} />
                        <section>
                            <Typography.Title level={5}>画布输入</Typography.Title>
                            <Typography.Paragraph>{selected ? `当前节点：${selected.title || selected.type}` : "选择一个画布节点查看其输入依赖"}</Typography.Paragraph>
                            {selectedSource ? <Typography.Paragraph>来源：{selectedSource.kind === "script" ? "脚本" : selectedSource.kind === "storyboard" ? "分镜" : "提示词"} {selectedSource.versionId.slice(0, 12)} · {selectedSourceCurrent ? "节点内容与当前版本一致" : "节点内容或上游版本已变化，请重新核对"}</Typography.Paragraph> : null}
                            {selectedAsset ? <Typography.Paragraph>视频来源：{project.deliverables.find((item) => item.id === selectedAsset.deliverableId && selectedAsset.projectId === project.id)?.name || "来源项目或交付物已变化，请重新核对"}</Typography.Paragraph> : null}
                            {selectedImage ? <Typography.Paragraph>母版来源：{selectedImageSource?.name || selectedImage.sourceId}；原始参考：{selectedImageSource?.reference_ids?.map((id) => project.sources.find((item) => item.id === id)?.name || id).join("、") || "未记录"} · {selectedImage.projectId === project.id && selectedImage.masterVersionId === currentMaster?.id && currentMaster?.asset_ids.includes(selectedImage.sourceId) && selectedImage.originalStorageKey === selected?.metadata?.storageKey ? "仍引用当前母版的加入时副本" : "图片内容或母版版本已变化，请重新核对"}</Typography.Paragraph> : null}
                            {selectedPromptImage ? <Typography.Paragraph>试图来源：{project.sources.find((item) => item.id === selectedPromptImage.sourceId)?.name || selectedPromptImage.sourceId} · 提示词版本 {selectedPromptImage.versionId.slice(0, 12)} · {selectedPromptImage.originalStorageKey !== selected?.metadata?.storageKey ? "节点图片已替换，请重新核对" : selectedPromptImage.projectId === project.id && project.prompt_adoption?.source_id === selectedPromptImage.sourceId ? "当前采用" : "未采用分支"}</Typography.Paragraph> : null}
                            {selectedPreview ? <Typography.Paragraph>画布试图来源：任务 {selectedPreview.taskId.slice(0, 12)} · {selectedPreview.originalStorageKey !== selected?.metadata?.storageKey ? "节点图片已替换，请重新核对" : staleNodeIds.has(selectedPreview.configNodeId) ? "原配置输入已变化，请重新核对" : selectedPreview.projectId === project.id && project.canvas_preview_adoption[selectedPreview.configNodeId] === selectedPreview.sourceId ? "当前采用" : "未采用"}</Typography.Paragraph> : null}
                            {inputs.length ? inputs.map((node) => <Tag key={node.id}>{node.type} · {node.title || node.id}</Tag>) : <Typography.Text type="secondary">等待连接</Typography.Text>}
                            {selected?.type === CanvasNodeType.Config ? <Typography.Paragraph className="mt-2">画布任务状态：{commerceNodeStatus(selected.id, inputs.length > 0, project.tasks, staleNodeIds.has(selected.id))}</Typography.Paragraph> : null}
                            {selected ? <Typography.Paragraph className="mt-2">节点费用：{selectedCosts.length} 笔任务；已确认实付 {selectedSpend.size ? [...selectedSpend].map(([unit, amount]) => `${amount.toFixed(2)} ${unit}`).join("、") : "暂无"}；待结算 {selectedCosts.filter((cost) => cost.actual == null).length} 笔；预估未知 {selectedCosts.filter((cost) => cost.estimate == null).length} 笔。</Typography.Paragraph> : null}
                            <Typography.Paragraph type="secondary" className="mt-2">连线仅编辑画布。任务提交前需查看实际输入和费用；未知费用不会自动提交。</Typography.Paragraph>
                            {selected?.type === CanvasNodeType.Config ? <Space direction="vertical" className="mt-2 w-full">
                                {selected.metadata?.generationMode === "video" ? <>
                                    <Select value={canvasFlovaShotId || ""} onChange={(value) => { setCanvasFlovaShotId(value || null); setCanvasFlovaOffer(null); setCanvasFlovaRequest(null); }} options={[{ value: "", label: "整片创作" }, ...(currentStoryboard?.shots || []).map((shot, index) => ({ value: shot.id, label: `镜头 ${index + 1}：${shot.visual.slice(0, 20)}` }))]} />
                                    {canvasFlovaShotId ? <Checkbox checked={canvasAutoRetry} onChange={(event) => setCanvasAutoRetry(event.target.checked)}>Flova 明确失败时自动重试，剩余最多 {canvasRemainingRetries} 次；每次可能产生费用</Checkbox> : null}
                                    <Button disabled={busy || !project.storyboard_approval || !canvasShotReady} onClick={() => void act(async () => {
                                        setCanvasFlovaOffer(null);
                                        setCanvasFlovaRequest(null);
                                        const context = await hydrateNodeGenerationContext(buildNodeGenerationContext(selected.id, nodes, connections, selected.metadata?.composerContent || selected.metadata?.prompt || ""));
                                        const { getMediaBlob } = await import("@/services/file-storage");
                                        const mediaReferences: FlovaCanvasContext["reference_media"] = [];
                                        for (const [kind, references] of [["video", context.referenceVideos], ["audio", context.referenceAudios]] as const) {
                                            for (const reference of references) {
                                                if (!reference.storageKey) throw new Error(`${kind === "video" ? "视频" : "音频"}参考节点需要先保存为本机素材`);
                                                const blob = await getMediaBlob(reference.storageKey);
                                                if (!blob) throw new Error("画布音视频原文件已丢失，请重新导入");
                                                const source = await studioApi.flovaMedia(project.id, reference.id, kind, blob, blob.type || reference.type);
                                                mediaReferences.push({ node_id: reference.id, source_id: source.id, sha256: source.sha256, kind });
                                            }
                                        }
                                        const request: FlovaCanvasContext = { canvas_project_id: canvasId, config_node_id: selected.id,
                                            connection_ids: connections.filter((edge) => edge.toNodeId === selected.id).map((edge) => edge.id), prompt: context.prompt,
                                            reference_images: await Promise.all(context.referenceImages.map(async (image) => ({ node_id: image.id,
                                                sha256: [...new Uint8Array(await crypto.subtle.digest("SHA-256", await (await fetch(image.dataUrl)).arrayBuffer()))].map((byte) => byte.toString(16).padStart(2, "0")).join("") }))),
                                            reference_media: mediaReferences };
                                        setCanvasFlovaRequest(request);
                                        setCanvasFlovaOffer(await (canvasFlovaShotId ? studioApi.flovaShotQuote(project.id, canvasFlovaShotId, request) : studioApi.flovaQuote(project.id, request)));
                                    })}>按当前视频连线查看 Flova {canvasFlovaShotId ? "单镜" : "整片"}输入</Button>
                                    {canvasFlovaOffer && canvasFlovaRequest?.config_node_id === selected.id ? <div>
                                        <Typography.Paragraph className="whitespace-pre-wrap">画布提示词：{canvasFlovaRequest.prompt}</Typography.Paragraph>
                                        <Typography.Paragraph>连线 {canvasFlovaRequest.connection_ids.length} 条；已审核参考图 {canvasFlovaRequest.reference_images.length} 张；音视频参考 {canvasFlovaRequest.reference_media.length} 份；已批准分镜 {String((canvasFlovaOffer.input_snapshot as { storyboard_id: string }).storyboard_id).slice(0, 12)}；费用：{canvasFlovaOffer.estimate == null ? "未知" : canvasFlovaOffer.estimate}</Typography.Paragraph>
                                        {(canvasFlovaOffer.input_snapshot as { assets: Array<{ id: string; kind: string; view: string; node_id?: string }> }).assets.map((item) => <Tag key={item.id}>{item.kind === "master" ? "母版图" : item.kind === "scene" ? "已采用场景图" : "场景图原始参考"} · {item.node_id ? nodeNames[item.node_id] || item.view : item.view}</Tag>)}
                                        {canvasFlovaRequest.reference_media.map((item) => <Tag key={item.source_id}>{item.kind === "video" ? "视频" : "音频"} · {nodeNames[item.node_id] || item.node_id}</Tag>)}
                                        <Button type="primary" disabled={busy} onClick={() => void act(async () => { if (canvasFlovaShotId) await studioApi.flovaShotRun(project.id, canvasFlovaShotId, canvasFlovaOffer.fingerprint, crypto.randomUUID(), canvasFlovaRequest, canvasAutoRetry ? canvasRemainingRetries : 0); else await studioApi.flovaRun(project.id, canvasFlovaOffer.fingerprint, crypto.randomUUID(), canvasFlovaRequest); setCanvasFlovaOffer(null); setCanvasFlovaRequest(null); })}>确认按此画布输入提交 Flova {canvasFlovaShotId ? "单镜" : "整片"}</Button>
                                    </div> : null}
                                </> : null}
                                {selected.metadata?.generationMode !== "video" ? <><Select value={previewRatio} onChange={setPreviewRatio} options={["1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"].map((ratio) => ({ value: ratio, label: ratio }))} />
                                <Button disabled={busy || !project.master_versions.length} onClick={() => void act(async () => {
                                    const context = await hydrateNodeGenerationContext(buildNodeGenerationContext(selected.id, nodes, connections, selected.metadata?.composerContent || selected.metadata?.prompt || ""));
                                    if (context.referenceImages.length !== 1 || context.referenceVideos.length || context.referenceAudios.length) throw new Error("试图需要恰好一张连线参考图，暂不支持视频或音频输入");
                                    const request: PreviewRequest = { canvas_project_id: canvasId, config_node_id: selected.id, reference_node_id: context.referenceImages[0].id,
                                        connection_ids: connections.filter((edge) => edge.toNodeId === selected.id).map((edge) => edge.id),
                                        prompt: context.prompt, ratio: previewRatio, reference_data_url: context.referenceImages[0].dataUrl };
                                    setPreviewRequest(request);
                                    setPreviewQuote(await studioApi.previewQuote(project.id, request));
                                })}>按当前连线查看试图输入</Button>
                                {previewQuote && previewRequest ? <><Typography.Paragraph className="whitespace-pre-wrap">提示词：{previewRequest.prompt}</Typography.Paragraph><Typography.Paragraph>参考图节点：{previewRequest.reference_node_id}；费用：{previewQuote.estimate == null ? "未知" : previewQuote.estimate}</Typography.Paragraph><Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.previewRun(project.id, { ...previewRequest, approved_fingerprint: previewQuote.fingerprint, request_id: crypto.randomUUID() }); setPreviewQuote(null); setPreviewRequest(null); })}>确认提交 SeeAny 单张试图</Button></> : null}</> : null}
                            </Space> : null}
                        </section>
                        <section>
                            <Typography.Title level={5}>试图任务</Typography.Title>
                            {project.tasks.filter((task) => task.kind === "preview").map((task) => {
                                const sourceId = task.asset_ids?.[0];
                                const nodeId = task.node_id || task.input_snapshot?.config_node_id;
                                const adopted = Boolean(sourceId && nodeId && project.canvas_preview_adoption[nodeId] === sourceId);
                                return <div key={task.id} className="mt-2"><Tag>{task.status}</Tag>画布试图 · {nodeNames[nodeId || ""] || nodeId || task.id.slice(0, 12)}
                                    <Button type="link" disabled={busy || !task.remote_id || task.status === "完成"} onClick={() => void act(() => studioApi.sync(project.id, task.id))}>同步状态</Button>
                                    {sourceId && task.source_id ? <Button type="link" onClick={() => void (async () => { try { const [original, generated] = await Promise.all([studioApi.sourceData(project.id, task.source_id!), studioApi.sourceData(project.id, sourceId)]); setPreviewComparison({ taskId: task.id, original: original.data_url, generated: generated.data_url }); } catch (cause) { setError(cause instanceof Error ? cause.message : "读取图片失败"); } })()}>对照查看</Button> : null}
                                    {sourceId && task.status === "待审核" ? <><Button type="link" disabled={busy || previewComparison?.taskId !== task.id} onClick={() => void act(() => studioApi.previewReview(project.id, task.id, sourceId, "采用"))}>核对后采用</Button><Button type="link" disabled={busy || previewComparison?.taskId !== task.id} onClick={() => { const reason = window.prompt("废图原因"); if (reason?.trim()) void act(() => studioApi.previewReview(project.id, task.id, sourceId, "废图", reason)); }}>废图</Button></> : null}
                                    {sourceId && nodeId && adopted ? <Button type="link" disabled={busy || staleNodeIds.has(nodeId) || task.input_snapshot?.brief_id !== project.brief_versions.at(-1)?.id || task.input_snapshot?.master_id !== project.master_versions.at(-1)?.id || nodes.some((node) => node.metadata?.commercePreview?.taskId === task.id && node.metadata.commercePreview.sourceId === sourceId)} onClick={() => void act(async () => {
                                        const configNode = nodes.find((node) => node.id === nodeId);
                                        if (!configNode || !task.input_snapshot) throw new Error("原配置节点已不存在");
                                        const context = await hydrateNodeGenerationContext(buildNodeGenerationContext(nodeId, nodes, connections, configNode.metadata?.composerContent || configNode.metadata?.prompt || ""));
                                        if (context.referenceImages.length !== 1 || context.referenceVideos.length || context.referenceAudios.length) throw new Error("原试图连线输入已变化，请重新核对");
                                        const current: PreviewRequest = { canvas_project_id: canvasId, config_node_id: nodeId, reference_node_id: context.referenceImages[0].id,
                                            connection_ids: connections.filter((edge) => edge.toNodeId === nodeId).map((edge) => edge.id), prompt: context.prompt,
                                            ratio: previewRatio, reference_data_url: context.referenceImages[0].dataUrl };
                                        const quote = await studioApi.previewQuote(project.id, current);
                                        const snapshot = quote.input_snapshot as NonNullable<StudioProject["tasks"][number]["input_snapshot"]>;
                                        if (previewInputChanged(task.input_snapshot, { briefId: snapshot.brief_id, masterId: snapshot.master_id, prompt: snapshot.prompt || "", referenceNodeId: snapshot.reference_node_id, connectionIds: snapshot.connection_ids || [], referenceSha256: snapshot.reference_sha256 })) throw new Error("原试图输入已变化，请重新核对");
                                        const asset = await studioApi.sourceData(project.id, sourceId);
                                        await onInsertImage(asset.data_url, "已采用画布试图", undefined, undefined, { projectId: project.id, taskId: task.id, sourceId, configNodeId: nodeId });
                                    })}>加入画布图片节点</Button> : null}
                                </div>;
                            })}
                            {previewComparison ? <div className="mt-2 grid grid-cols-2 gap-2"><div><Typography.Text>母版参考</Typography.Text><img src={previewComparison.original} alt="母版参考" className="w-full" /></div><div><Typography.Text>试图候选</Typography.Text><img src={previewComparison.generated} alt="试图候选" className="w-full" /></div></div> : null}
                        </section>
                        {project.tasks.some((task) => task.status === "待核对" && !task.remote_id) ? <section>
                            <Typography.Title level={5}>中断任务核对</Typography.Title>
                            <Typography.Paragraph type="secondary">以下任务没有可信的远端标识。请先在供应商后台检查；确认不存在对应任务后，可记录核对依据并标记失败。此操作不会自动重新提交或改变费用流水。</Typography.Paragraph>
                            {project.tasks.filter((task) => task.status === "待核对" && !task.remote_id).map((task) => <div key={task.id} className="mt-2 border-t pt-2"><Tag>{task.provider}</Tag>{task.kind || "任务"} · {task.id.slice(0, 12)}{task.interrupted_at ? " · 本机运行中断" : " · 提交结果不明"}<Button type="link" disabled={busy} onClick={() => { setResolvingTaskId(task.id); setResolutionNote(""); }}>记录核对结果</Button>{resolvingTaskId === task.id ? <div><Input.TextArea rows={2} value={resolutionNote} onChange={(event) => setResolutionNote(event.target.value)} placeholder="填写供应商后台核对依据，确认远端没有该任务" /><Button type="primary" disabled={busy || !resolutionNote.trim()} onClick={() => void act(async () => { await studioApi.resolveUnidentifiedTask(project.id, task.id, resolutionNote); setResolvingTaskId(null); setResolutionNote(""); })}>确认远端无此任务，标记失败</Button><Button onClick={() => setResolvingTaskId(null)}>取消</Button></div> : null}</div>)}
                        </section> : null}
                        <CommerceCostSection project={project} busy={busy} act={act} nodeNames={nodeNames} />
                        <section><Typography.Title level={5}>项目备份与归档</Typography.Title><Typography.Paragraph type="secondary">将当前画布、浏览器媒体文件、本地原图、版本和任务记录一起打包。恢复入口位于画布列表的“导入”。</Typography.Paragraph><Space wrap><Button disabled={busy} onClick={() => void act(exportWholeProject)}>下载完整项目备份</Button><Button disabled={busy} onClick={() => void act(() => studioApi.archive(project.id, !project.archived_at))}>{project.archived_at ? "恢复项目" : "归档项目"}</Button></Space>{project.archived_at ? <Typography.Paragraph type="secondary" className="mt-2">已本地归档；资料、版本和画布仍可查看、编辑与复用。</Typography.Paragraph> : null}</section>
                    </>
                )}
            </Space>
        </Drawer>
    );
}
