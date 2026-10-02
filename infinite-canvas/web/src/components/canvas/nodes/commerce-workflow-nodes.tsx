import { useCallback, useEffect, useRef, useState } from "react";
import localforage from "localforage";
import { Clapperboard, FileImage, FileText, Film, Images, LoaderCircle, MessageSquareText, Sparkles } from "lucide-react";

import { registerNodeDefinitions } from "@/lib/canvas/node-registry";
import type { CanvasAgentOp } from "@/lib/canvas/canvas-agent-ops";
import { workflowGraph, workflowKinds } from "@/lib/canvas/commerce-workflow-graph";
import { uploadImage } from "@/services/image-storage";
import { uploadMediaFile } from "@/services/file-storage";
import { studioApi, type StudioProject, type StudioQuote, type VideoShot, type WorkflowField } from "@/services/api/commerce-studio";
import type { CanvasNodeContext, CanvasNodeDefinition } from "@/types/canvas-plugin";

const projectPromises = new Map<string, Promise<string>>();
function canvasId() { return window.location.pathname.split("/")[2] || ""; }
function studioId() {
    const id = canvasId();
    if (!projectPromises.has(id)) projectPromises.set(id, (async () => {
        const key = `commerce-studio:${id}`;
        const found = await localforage.getItem<string>(key);
        if (found) return found;
        const created = await studioApi.create("画布商品工作流");
        await localforage.setItem(key, created.id);
        return created.id;
    })());
    return projectPromises.get(id)!;
}
function errorText(error: unknown) { return error instanceof Error ? error.message : String(error); }
function graph(ctx: CanvasNodeContext) { return workflowGraph(ctx.getNodes(), ctx.getConnections(), ctx.node.id); }

async function materializeResults(ctx: CanvasNodeContext, projectId: string, project: StudioProject) {
    const tasks = project.tasks.filter((task) => task.node_id === ctx.node.id &&
        (task.kind === "workflow_image" || task.kind === "workflow_views" || task.kind === "workflow_gallery"));
    const ops: CanvasAgentOp[] = [];
    const known = new Set(ctx.getNodes().map((node) => node.id));
    for (const task of tasks) for (const sourceId of task.asset_ids || []) {
        const id = `commerce:result:${task.id}:${sourceId}`;
        if (known.has(id)) continue;
        known.add(id);
        const source = await studioApi.sourceData(projectId, sourceId);
        const record = project.sources.find((item) => item.id === sourceId);
        const image = await uploadImage(source.data_url);
        const index = (task.asset_ids || []).indexOf(sourceId);
        ops.push(
            { type: "add_node", id, nodeType: workflowKinds.result, title: `${ctx.node.title} · ${record?.view_label || `结果 ${index + 1}`}`,
                position: { x: ctx.node.position.x + ctx.node.width + 80, y: ctx.node.position.y + index * 260 },
                metadata: { content: image.url, storageKey: image.storageKey, naturalWidth: image.width,
                    naturalHeight: image.height, mimeType: image.mimeType, bytes: image.bytes, status: "success",
                    workflowSourceId: sourceId, workflowTaskId: task.id,
                    workflowPrompt: task.provider_request?.prompt || task.input_snapshot?.inputs?.find((item) => item.port === "prompt")?.text,
                    workflowReferenceIds: record?.reference_ids, workflowModel: task.model,
                    workflowInferred: task.kind === "workflow_views" } },
            { type: "connect_nodes", fromNodeId: ctx.node.id, toNodeId: id },
        );
    }
    if (ops.length) ctx.applyOps(ops);
}

async function materializeVideoResults(ctx: CanvasNodeContext, projectId: string, project: StudioProject) {
    const tasks = project.tasks.filter((task) => task.node_id === ctx.node.id && (task.kind === "commerce:shot" || task.kind === "commerce:compose") && task.status === "待审核" && task.deliverable_id);
    const known = new Set(ctx.getNodes().map((node) => node.id));
    const ops: CanvasAgentOp[] = [];
    for (const task of tasks) {
        const id = `commerce:video-result:${task.id}`;
        if (known.has(id)) continue;
        const blob = await studioApi.videoBlob(projectId, task.deliverable_id!);
        const media = await uploadMediaFile(blob, "commerce-video");
        ops.push({ type: "add_node", id, nodeType: workflowKinds.videoResult, title: `${ctx.node.title} · 视频结果`, position: { x: ctx.node.position.x + ctx.node.width + 80, y: ctx.node.position.y }, metadata: {
            content: media.url, storageKey: media.storageKey, bytes: media.bytes, mimeType: media.mimeType, naturalWidth: media.width, naturalHeight: media.height,
            durationMs: media.durationMs, status: "success", workflowTaskId: task.id, workflowDeliverableId: task.deliverable_id, workflowShotIndex: task.input_snapshot?.shot_index,
        } }, { type: "connect_nodes", fromNodeId: ctx.node.id, toNodeId: id });
        known.add(id);
    }
    if (ops.length) ctx.applyOps(ops);
}

function WorkflowContent({ ctx }: { ctx: CanvasNodeContext }) {
    const [busy, setBusy] = useState(false);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState("");
    const currentCtx = useRef(ctx);
    currentCtx.current = ctx;
    const sourceId = ctx.node.metadata?.workflowSourceId;
    const versionId = ctx.node.metadata?.workflowVersionId;
    const isUpload = ctx.node.type === workflowKinds.upload;
    const isImageResult = ctx.node.type === workflowKinds.result;
    const isVideoResult = ctx.node.type === workflowKinds.videoResult;
    const isResult = isImageResult || isVideoResult;
    useEffect(() => {
        if (![workflowKinds.generate, workflowKinds.views, workflowKinds.gallery, workflowKinds.shot, workflowKinds.compose].includes(ctx.node.type as never)) return;
        let disposed = false;
        let running = false;
        const refresh = async () => {
            if (running || disposed) return;
            running = true;
            try {
                const projectId = await studioId();
                let project = await studioApi.get(projectId);
                const pendingTasks = project.tasks.filter((task) => task.node_id === ctx.node.id && (task.provider === "SeeAny" || task.provider === "Flova") &&
                    task.remote_id && !["待审核", "完成", "失败"].includes(task.status));
                setPending(pendingTasks.length > 0);
                for (const task of pendingTasks) await studioApi.sync(projectId, task.id);
                if (pendingTasks.length) project = await studioApi.get(projectId);
                if (pendingTasks.length) setPending(project.tasks.some((task) => task.node_id === ctx.node.id && (task.provider === "SeeAny" || task.provider === "Flova") && task.remote_id && !["待审核", "完成", "失败"].includes(task.status)));
                if (!disposed) await materializeResults(currentCtx.current, projectId, project);
                if (!disposed) await materializeVideoResults(currentCtx.current, projectId, project);
                if (!disposed) setError("");
            } catch (cause) {
                if (!disposed) setError(errorText(cause));
            } finally {
                running = false;
            }
        };
        void refresh();
        const timer = window.setInterval(() => void refresh(), 10_000);
        return () => { disposed = true; window.clearInterval(timer); };
    }, [ctx.node.id, ctx.node.type]);
    const upload = async (file?: File) => {
        if (!file) return;
        setBusy(true); setError("");
        try {
            const id = await studioId();
            const source = await studioApi.source(id, file);
            const image = await uploadImage(file);
            ctx.updateMetadata({ workflowSourceId: source.id, content: image.url, storageKey: image.storageKey,
                mimeType: image.mimeType, bytes: image.bytes, naturalWidth: image.width, naturalHeight: image.height,
                status: "success" });
        } catch (e) { setError(errorText(e)); }
        finally { setBusy(false); }
    };
    return <div className="flex h-full flex-col gap-2 overflow-auto p-3 text-sm" style={{ color: ctx.theme.node.text }}>
        {(isUpload || isImageResult) && ctx.node.metadata?.content ? <img src={ctx.node.metadata.content} alt={ctx.node.title} className="max-h-36 w-full object-contain" /> : null}
        {isVideoResult && ctx.node.metadata?.content ? <video src={ctx.node.metadata.content} controls className="max-h-36 w-full object-contain" /> : null}
        {isUpload ? <label className="cursor-pointer underline" onMouseDown={(event) => event.stopPropagation()}><input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" disabled={busy} onChange={(event) => void upload(event.target.files?.[0])} />{busy ? "保存中…" : sourceId ? "更换商品原图" : "选择商品原图"}</label> : null}
        {isImageResult ? <><span>来源任务：{ctx.node.metadata?.workflowTaskId?.slice(0, 12)}</span>
            <span>参考图：{ctx.node.metadata?.workflowReferenceIds?.map((id) => id.slice(0, 8)).join("、") || "无"}</span>
            <span>模型：{ctx.node.metadata?.workflowModel || "未知"} · 费用未知</span>
            {ctx.node.metadata?.workflowInferred ? <span>推断视角，未核实结构</span> : null}
            {ctx.node.metadata?.workflowPrompt ? <span title={ctx.node.metadata.workflowPrompt}>提示词：{ctx.node.metadata.workflowPrompt.slice(0, 48)}</span> : null}
            <span>结果：{ctx.node.metadata?.workflowDecision || "待挑选"}</span>
            <div className="flex gap-3">{(["采用", "废图"] as const).map((decision) => <button key={decision} type="button" className="underline" onMouseDown={(event) => event.stopPropagation()} onClick={() => void (async () => {
                try { await studioApi.workflowReview(await studioId(), ctx.node.metadata!.workflowTaskId!, sourceId!, decision); ctx.updateMetadata({ workflowDecision: decision }); }
                catch (e) { setError(errorText(e)); }
            })()}>{decision}</button>)}</div></> : null}
        {isVideoResult ? <><span>来源任务：{ctx.node.metadata?.workflowTaskId?.slice(0, 12)}</span><span>分镜：{(ctx.node.metadata?.workflowShotIndex ?? 0) + 1} · 4 秒</span></> : null}
        {!isUpload && !isResult ? <><span>{versionId ? `已确认版本 ${versionId.slice(0, 12)}` : "尚无确认输出"}</span>
            <button type="button" className="self-start underline" onMouseDown={(event) => event.stopPropagation()} onClick={() => ctx.openPanel()}>查看输入 / 编辑 / 生成</button></> : null}
        {pending ? <span className="flex items-center gap-1"><LoaderCircle className="size-4 animate-spin" />远端生成中，等待结果…</span> : null}
        {error ? <span className="text-red-500">{error}</span> : null}
    </div>;
}

function WorkflowPanel({ ctx, onClose }: { ctx: CanvasNodeContext; onClose: () => void }) {
    const [project, setProject] = useState<StudioProject | null>(null);
    const [offer, setOffer] = useState<StudioQuote | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [draft, setDraft] = useState(ctx.node.metadata?.workflowDraft || "");
    const [view, setView] = useState(ctx.node.metadata?.workflowView || "正面");
    const [ratio, setRatio] = useState(ctx.node.metadata?.workflowRatio || "1:1");
    const [galleryKinds, setGalleryKinds] = useState(ctx.node.metadata?.workflowGalleryKinds || ["商品白底图", "亚马逊主图"]);
    const [size, setSize] = useState(ctx.node.metadata?.workflowSize || "1K");
    const [fields, setFields] = useState<WorkflowField[]>([]);
    const [candidateId, setCandidateId] = useState<string | undefined>();
    const [resolvingTaskId, setResolvingTaskId] = useState<string | null>(null);
    const [resolutionNote, setResolutionNote] = useState("");
    const isDetails = ctx.node.type === workflowKinds.details;
    const isPrompt = ctx.node.type === workflowKinds.prompt;
    const isGallery = ctx.node.type === workflowKinds.gallery;
    const isScript = ctx.node.type === workflowKinds.script;
    const isShot = ctx.node.type === workflowKinds.shot;
    const isCompose = ctx.node.type === workflowKinds.compose;
    const isText = isDetails || isPrompt;
    const load = useCallback(async () => {
        const id = await studioId();
        const next = await studioApi.get(id);
        setProject(next);
        await materializeResults(ctx, id, next);
        await materializeVideoResults(ctx, id, next);
    }, [ctx]);
    useEffect(() => { void load().catch((e) => setError(errorText(e))); }, [load]);
    const taskList = (project?.tasks || []).filter((task) => task.node_id === ctx.node.id).slice().reverse();
    useEffect(() => {
        if (!isDetails || fields.length) return;
        const candidate = taskList.find((task) => task.kind === "workflow_text" && task.status === "待审核" && task.candidates?.length);
        if (candidate?.candidates) setFields(candidate.candidates.map((field) => ({ ...field, candidate_task_id: candidate.id })));
    }, [isDetails, fields.length, taskList]);
    useEffect(() => {
        if (!isScript || draft.trim()) return;
        const candidate = taskList.find((task) => task.kind === workflowKinds.script && task.status === "待审核" && task.candidate_text);
        if (candidate?.candidate_text) setDraft(candidate.candidate_text);
    }, [isScript, draft, taskList]);
    const versions = (project?.workflow_versions || []).filter((v) => v.node_id === ctx.node.id);
    const inputEdges = ctx.getConnections().filter((edge) => edge.toNodeId === ctx.node.id);
    const patchField = (index: number, patch: Partial<WorkflowField>) => setFields((current) => current.map((item, i) => i === index ? { ...item, ...patch } : item));
    const guarded = async (action: () => Promise<void>) => {
        setBusy(true); setError(""); setNotice("正在提交请求…");
        try { await action(); await load(); setNotice(isText ? "DeepSeek 已返回候选，请检查并确认字段。" : "请求已提交。生成完成后会自动回填画布。"); } catch (e) { setNotice(""); setError(errorText(e)); }
        finally { setBusy(false); }
    };
    const currentGraph = () => {
        const g = graph(ctx);
        const node = g.nodes.find((item) => item.id === ctx.node.id);
        if (node) { node.draft = draft; node.view = view; node.ratio = ctx.node.metadata?.workflowVideoRatio || ratio; node.model = ctx.node.metadata?.workflowVideoModel || ctx.node.metadata?.workflowModel;
            node.gallery_kinds = galleryKinds; node.size = size; node.main_ratio = ctx.node.metadata?.workflowMainRatio || "1:1"; node.detail_ratio = ctx.node.metadata?.workflowDetailRatio || "3:4";
            node.product_name = ctx.node.metadata?.workflowProductName; node.platform = ctx.node.metadata?.workflowPlatform; node.market = ctx.node.metadata?.workflowMarket; node.language = ctx.node.metadata?.workflowLanguage || "中文"; node.style = ctx.node.metadata?.workflowStyle; }
        if (node) { node.shot_index = ctx.node.metadata?.workflowShotIndex; node.task_id = ctx.node.metadata?.workflowTaskId; node.version_id = ctx.node.metadata?.workflowVersionId; node.deliverable_id = ctx.node.metadata?.workflowDeliverableId; }
        return g;
    };
    const quote = () => guarded(async () => {
        const next = await studioApi.workflowQuote(await studioId(), ctx.node.id, currentGraph());
        setOffer(next);
    });
    const run = () => guarded(async () => {
        const current = currentGraph();
        const id = await studioId();
        const approved = await studioApi.workflowQuote(id, ctx.node.id, current);
        await studioApi.workflowRun(id, ctx.node.id, current, approved.fingerprint, crypto.randomUUID());
        setOffer(null);
    });
    const runVideo = () => guarded(async () => {
        const id = await studioId();
        const current = currentGraph();
        const approved = await studioApi.videoQuote(id, ctx.node.id, current);
        await studioApi.videoRun(id, ctx.node.id, current, approved.fingerprint, crypto.randomUUID());
    });
    const confirmScript = () => guarded(async () => {
        const parsed = JSON.parse(draft) as { shots?: VideoShot[] };
        if (!Array.isArray(parsed.shots) || parsed.shots.length !== 4) throw new Error("请先生成包含 4 段分镜的 JSON 脚本");
        const version = await studioApi.videoConfirm(await studioId(), { node_id: ctx.node.id, kind: workflowKinds.script, shots: parsed.shots });
        ctx.updateMetadata({ workflowVersionId: version.id, workflowDraft: draft });
    });
    const confirm = () => guarded(async () => {
        const version = await studioApi.workflowConfirm(await studioId(), { node_id: ctx.node.id,
            kind: ctx.node.type, text: draft, fields: isDetails ? fields : undefined, candidate_task_id: isPrompt ? candidateId : undefined });
        ctx.updateMetadata({ workflowVersionId: version.id });
        setCandidateId(undefined);
    });
    const accent = ctx.theme.node.muted;
    return <div className="w-[430px] max-h-[72vh] overflow-auto rounded-xl border p-4 text-sm shadow-xl" style={{ background: ctx.theme.node.panel, borderColor: ctx.theme.node.stroke, color: ctx.theme.node.text }} onMouseDown={(event) => event.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between"><b>{ctx.node.title}</b><button type="button" onClick={onClose}>关闭</button></div>
        {notice ? <div className="mb-2 rounded border border-blue-400/40 bg-blue-500/10 p-2 text-blue-300">{busy ? <LoaderCircle className="mr-1 inline size-4 animate-spin" /> : null}{notice}</div> : null}
        {error ? <div className="mb-2 rounded border border-red-400/40 bg-red-500/10 p-2 text-red-300">请求失败：{error}</div> : null}
        <div className="mb-3 text-xs" style={{ color: accent }}>费用仅作参考：{offer?.estimate == null ? "未知" : `${offer.estimate} ${offer.currency || ""}`}，不影响生成。点击生成按钮后会直接提交任务。</div>
        <div className="mb-3 space-y-1"><b>实际连入</b>{inputEdges.length ? inputEdges.map((edge) => {
            const source = ctx.getNode(edge.fromNodeId);
            const latest = (project?.workflow_versions || []).filter((v) => v.node_id === source?.id).at(-1);
            const selected = edge.selectedVersionId;
            return <div key={edge.id} className="rounded p-2" style={{ background: ctx.theme.node.fill }}>
                <span>{source?.title || edge.fromNodeId} → {edge.targetPort}</span>
                {selected ? <span className="ml-1">版本 {selected.slice(0, 8)}</span> : null}
                {latest && selected !== latest.id ? <span className="ml-1 text-amber-600">有新版本可用</span> : null}
                {latest ? <select className="ml-2 bg-transparent" value={selected || ""} onChange={(event) => {
                    window.dispatchEvent(new CustomEvent("commerce-workflow-select-version", { detail: { edgeId: edge.id, versionId: event.target.value } }));
                    setOffer(null);
                }}><option value="">选择确认版本</option>{(project?.workflow_versions || []).filter((v) => v.node_id === source?.id).map((v) => <option key={v.id} value={v.id}>{v.id.slice(0, 8)} · {v.created}</option>)}</select> : null}
            </div>;
        }) : <div>暂无连线</div>}</div>
        {isText ? <><div className="mb-2">{isDetails ? "商品信息逐项确认" : "提示词草稿"}</div>
            {isPrompt ? <textarea className="mb-2 h-28 w-full rounded border bg-transparent p-2" value={draft} onChange={(event) => { setDraft(event.target.value); ctx.updateMetadata({ workflowDraft: event.target.value }); setOffer(null); }} /> : <>
                {fields.map((field, index) => <div key={index} className="mb-2 grid grid-cols-[1fr_2fr] gap-1">
                    <input className="rounded border bg-transparent px-1" placeholder="字段" value={field.field} onChange={(event) => patchField(index, { field: event.target.value })} />
                    <input className="rounded border bg-transparent px-1" placeholder="内容" value={field.value} onChange={(event) => patchField(index, { value: event.target.value })} />
                    <select className="col-span-2 bg-transparent" value={field.status} onChange={(event) => patchField(index, { status: event.target.value as WorkflowField["status"] })}><option>待核实</option><option>已知事实</option><option>创意假设</option></select>
                </div>)}
                <button type="button" className="underline" onClick={() => setFields((items) => [...items, { field: "", value: "", status: "待核实" }])}>添加手写字段</button>
                <textarea className="mt-2 h-16 w-full rounded border bg-transparent p-2" placeholder="给模型的补充要求（可选）" value={draft} onChange={(event) => { setDraft(event.target.value); ctx.updateMetadata({ workflowDraft: event.target.value }); setOffer(null); }} />
            </>}
            <div className="my-2">{taskList.filter((t) => t.kind === "workflow_text" && t.status === "待审核").map((task) => <div key={task.id} className="mb-2 rounded border p-2">
                <b>候选 {task.id.slice(0, 8)}</b>{isDetails && !task.candidates?.length ? <p>模型未识别出可用字段，可手写后确认。</p> : null}{isDetails && task.candidates?.length ? <button type="button" className="ml-2 underline" onClick={() => setFields(task.candidates!.map((field) => ({ ...field, candidate_task_id: task.id })))}>选取全部结构化信息</button> : null}{task.candidates?.map((field, index) => <div key={index} className="flex justify-between gap-2"><span>{field.field}：{field.value}</span><button type="button" className="underline" onClick={() => setFields((current) => [...current, { ...field, candidate_task_id: task.id }])}>选取</button></div>)}
                {isPrompt && task.candidate_text ? <><p className="whitespace-pre-wrap">{task.candidate_text}</p><button type="button" className="underline" onClick={() => { setDraft(task.candidate_text!); setCandidateId(task.id); ctx.updateMetadata({ workflowDraft: task.candidate_text }); setOffer(null); }}>选此整份</button></> : null}
            </div>)}</div>
            <button type="button" className="mr-4 underline" disabled={busy} onClick={() => void guarded(async () => { const health = await studioApi.health(); if (!health.deepseek) throw new Error("DeepSeek 未配置，仍可手写确认"); const current = currentGraph(); const approved = await studioApi.workflowQuote(await studioId(), ctx.node.id, current); await studioApi.workflowRun(await studioId(), ctx.node.id, current, approved.fingerprint, crypto.randomUUID()); })}>{busy ? "DeepSeek 生成中…" : "生成候选 · DeepSeek"}</button>
            <button type="button" className="underline" disabled={busy} onClick={() => void confirm()}>确认新版本</button>
            {versions.length ? <p className="mt-2 text-xs">已确认 {versions.length} 版；当前输出 {ctx.node.metadata?.workflowVersionId?.slice(0, 8)}</p> : null}
        </> : isScript ? <><div className="mb-2">四段分镜脚本（DeepSeek）</div><textarea className="h-52 w-full rounded border bg-transparent p-2 font-mono text-xs" placeholder='生成后会得到包含 shots 数组的 JSON；也可以手工修改' value={draft} onChange={(event) => { setDraft(event.target.value); ctx.updateMetadata({ workflowDraft: event.target.value }); }} /><div className="mt-3 flex gap-4"><button type="button" className="underline" disabled={busy} onClick={() => void runVideo()}>{busy ? "DeepSeek 生成中…" : "生成四段分镜"}</button><button type="button" className="underline" disabled={busy || !draft.trim()} onClick={() => void confirmScript()}>确认脚本版本</button></div></> : isShot || isCompose ? <><div className="mb-2">{isShot ? `第 ${(ctx.node.metadata?.workflowShotIndex ?? 0) + 1} 段分镜视频` : "Flova 合成（约 15 秒）"}</div>{isShot ? <label>分镜编号 <select className="ml-2 bg-transparent" value={ctx.node.metadata?.workflowShotIndex ?? 0} onChange={(event) => ctx.updateMetadata({ workflowShotIndex: Number(event.target.value) })}>{[0, 1, 2, 3].map((index) => <option key={index} value={index}>{index + 1}</option>)}</select></label> : <p>需要连接四个已生成的分镜视频结果。</p>}<p className="mt-2 text-xs">Flova 任务会在后台运行，完成后自动生成视频结果节点。</p><button type="button" className="mt-3 underline" disabled={busy} onClick={() => void runVideo()}>{busy ? "Flova 生成中…" : isShot ? "生成 4 秒分镜视频" : "合成约 15 秒视频"}</button></> : <>
            {ctx.node.type === workflowKinds.views ? <label>视角 <select className="ml-2 bg-transparent" value={view} onChange={(event) => { setView(event.target.value); ctx.updateMetadata({ workflowView: event.target.value }); setOffer(null); }}><option>正面</option><option>侧面</option><option>背面</option></select></label> : null}
            {ctx.node.type === workflowKinds.generate ? <label>比例 <select className="ml-2 bg-transparent" value={ratio} onChange={(event) => { setRatio(event.target.value); ctx.updateMetadata({ workflowRatio: event.target.value }); setOffer(null); }}>{["1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"].map((r) => <option key={r}>{r}</option>)}</select></label> : null}
            {isGallery ? <div className="space-y-2">
                <input className="w-full rounded border bg-transparent p-1" placeholder="商品名称" value={ctx.node.metadata?.workflowProductName || ""} onChange={(event) => { ctx.updateMetadata({ workflowProductName: event.target.value }); setOffer(null); }} />
                <textarea className="w-full rounded border bg-transparent p-1" placeholder="商品信息与核心卖点" value={draft} onChange={(event) => { setDraft(event.target.value); ctx.updateMetadata({ workflowDraft: event.target.value }); setOffer(null); }} />
                {([['电商平台', 'workflowPlatform'], ['目标市场', 'workflowMarket'], ['文案语种', 'workflowLanguage'], ['视觉风格', 'workflowStyle']] as const).map(([label, key]) => <input key={key} className="w-full rounded border bg-transparent p-1" placeholder={label} value={ctx.node.metadata?.[key] || ""} onChange={(event) => { ctx.updateMetadata({ [key]: event.target.value }); setOffer(null); }} />)}
                <label>模型 <select className="ml-2 bg-transparent" value={ctx.node.metadata?.workflowModel || "nano-banana-pro"} onChange={(event) => { ctx.updateMetadata({ workflowModel: event.target.value }); setOffer(null); }}><option value="nano-banana-pro">Banana Pro</option><option value="gpt-image-2">GPT image-2</option></select></label>
                <label className="block">分辨率 <select className="ml-2 bg-transparent" value={size} onChange={(event) => { setSize(event.target.value); ctx.updateMetadata({ workflowSize: event.target.value }); setOffer(null); }}><option>1K</option><option>2K</option></select></label>
                {([['主图比例', 'workflowMainRatio'], ['详情页比例', 'workflowDetailRatio']] as const).map(([label, key]) => <label key={key} className="block">{label} <select className="ml-2 bg-transparent" value={ctx.node.metadata?.[key] || (key === 'workflowMainRatio' ? '1:1' : '3:4')} onChange={(event) => { ctx.updateMetadata({ [key]: event.target.value }); setOffer(null); }}>{['1:1', '3:4', '4:3'].map((value) => <option key={value}>{value}</option>)}</select></label>)}
                <div>出图类型（最多 6 项）</div><div className="flex flex-wrap gap-2">{["商品白底图", "亚马逊主图", "细节特写", "产品多角度", "营销主图海报", "大促营销主图", "首屏视觉图", "产品代言互动", "客户痛点展示", "核心卖点图", "产品场景展示图", "试穿试戴场景"].map((kind) => <label key={kind}><input type="checkbox" checked={galleryKinds.includes(kind)} onChange={(event) => { const next = event.target.checked ? [...galleryKinds, kind] : galleryKinds.filter((item) => item !== kind); setGalleryKinds(next); ctx.updateMetadata({ workflowGalleryKinds: next }); setOffer(null); }} /> {kind}</label>)}</div>
            </div> : null}
            <div className="mt-3 flex flex-wrap items-center gap-4"><button type="button" className="underline" disabled={busy} onClick={() => void run()}>{busy ? "SeeAny 生成中…" : "生成图片"}</button>{notice ? <span className="flex items-center gap-1 text-blue-300">{busy ? <LoaderCircle className="size-4 animate-spin" /> : null}{notice}</span> : null}{error ? <span className="text-red-300">失败：{error}</span> : null}</div>
            {offer ? <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(offer.input_snapshot, null, 2)}</pre> : null}
        </>}
        {taskList.length ? <div className="mt-4 border-t pt-2"><b>任务</b>{taskList.map((task) => <div key={task.id} className="mt-1 text-xs">{task.id.slice(0, 8)} · {task.status} · {task.remote_id ? `远端 ${task.remote_id.slice(0, 12)}` : "无远端 ID"}
            {task.error ? ` · ${task.error}` : ""} {task.provider === "SeeAny" && task.remote_id && task.status !== "完成" ? <button type="button" className="underline" disabled={busy} onClick={() => void guarded(async () => { await studioApi.sync(await studioId(), task.id); })}>同步</button> : null}
            {task.status === "待核对" && !task.remote_id ? <><button type="button" className="ml-2 underline" disabled={busy} onClick={() => { setResolvingTaskId(task.id); setResolutionNote(""); }}>记录远端核对</button>{resolvingTaskId === task.id ? <div className="mt-1"><input className="w-full rounded border bg-transparent px-1" placeholder="填写供应商后台确认无任务的依据" value={resolutionNote} onChange={(event) => setResolutionNote(event.target.value)} /><button type="button" className="mt-1 underline" disabled={busy || !resolutionNote.trim()} onClick={() => void guarded(async () => { await studioApi.resolveUnidentifiedTask(await studioId(), task.id, resolutionNote); setResolvingTaskId(null); setResolutionNote(""); })}>确认远端无任务</button></div> : null}</> : null}</div>)}</div> : null}
    </div>;
}

const specs: Array<[string, string, React.ReactNode, string]> = [
    [workflowKinds.upload, "上传商品图", <FileImage className="size-5" />, "保存本机原图并输出图片资产"],
    [workflowKinds.details, "生成商品详细信息", <FileText className="size-5" />, "从相连原图生成逐项候选并确认"],
    [workflowKinds.views, "生成白底三视图", <Images className="size-5" />, "从相连原图生成指定视角"],
    [workflowKinds.prompt, "提示词", <MessageSquareText className="size-5" />, "手写或生成后确认版本"],
    [workflowKinds.generate, "生成商品图", <Sparkles className="size-5" />, "读取相连图片和确认提示词"],
    [workflowKinds.gallery, "电商套图", <Images className="size-5" />, "按模板批量生成主图、详情和卖点图"],
    [workflowKinds.result, "商品图结果", <FileImage className="size-5" />, "独立生成结果"],
    [workflowKinds.script, "分镜脚本（DeepSeek）", <Clapperboard className="size-5" />, "生成四段 4 秒分镜脚本"],
    [workflowKinds.shot, "分镜视频（Flova）", <Film className="size-5" />, "按编号生成 4 秒视频"],
    [workflowKinds.compose, "Flova 合成", <Film className="size-5" />, "合成四个分镜为约 15 秒视频"],
    [workflowKinds.videoResult, "视频结果", <Film className="size-5" />, "已生成视频结果"],
];
let registered = false;
export function registerCommerceWorkflowNodes() {
    if (registered) return;
    registered = true;
    registerNodeDefinitions(specs.map(([type, title, icon, description]): CanvasNodeDefinition => ({
        type, title, icon, description, defaultSize: { width: 290, height: 200 },
        defaultMetadata: type === workflowKinds.views ? { workflowView: "正面" } : type === workflowKinds.generate ? { workflowRatio: "1:1" } : type === workflowKinds.shot ? { workflowShotIndex: 0, workflowVideoModel: "flova", workflowVideoRatio: "9:16" } : {},
        showInCreateMenu: type !== workflowKinds.result && type !== workflowKinds.videoResult, hasSourceHandle: type !== workflowKinds.generate,
        hidePanel: type === workflowKinds.upload || type === workflowKinds.result || type === workflowKinds.videoResult,
        Content: WorkflowContent, Panel: type === workflowKinds.upload || type === workflowKinds.result || type === workflowKinds.videoResult ? undefined : WorkflowPanel,
    })), "commerce-workflow");
}
