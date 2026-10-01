import { useCallback, useEffect, useRef, useState } from "react";
import localforage from "localforage";
import { FileImage, FileText, Images, MessageSquareText, Sparkles } from "lucide-react";

import { registerNodeDefinitions } from "@/lib/canvas/node-registry";
import type { CanvasAgentOp } from "@/lib/canvas/canvas-agent-ops";
import { workflowGraph, workflowKinds } from "@/lib/canvas/commerce-workflow-graph";
import { uploadImage } from "@/services/image-storage";
import { studioApi, type StudioProject, type StudioQuote, type WorkflowField } from "@/services/api/commerce-studio";
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
        (task.kind === "workflow_image" || task.kind === "workflow_views"));
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

function WorkflowContent({ ctx }: { ctx: CanvasNodeContext }) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const currentCtx = useRef(ctx);
    currentCtx.current = ctx;
    const sourceId = ctx.node.metadata?.workflowSourceId;
    const versionId = ctx.node.metadata?.workflowVersionId;
    const isUpload = ctx.node.type === workflowKinds.upload;
    const isResult = ctx.node.type === workflowKinds.result;
    useEffect(() => {
        if (ctx.node.type !== workflowKinds.generate && ctx.node.type !== workflowKinds.views) return;
        let disposed = false;
        let running = false;
        const refresh = async () => {
            if (running || disposed) return;
            running = true;
            try {
                const projectId = await studioId();
                let project = await studioApi.get(projectId);
                const pending = project.tasks.filter((task) => task.node_id === ctx.node.id && task.provider === "SeeAny" &&
                    task.remote_id && !["待审核", "完成", "失败"].includes(task.status));
                for (const task of pending) await studioApi.sync(projectId, task.id);
                if (pending.length) project = await studioApi.get(projectId);
                if (!disposed) await materializeResults(currentCtx.current, projectId, project);
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
        {(isUpload || isResult) && ctx.node.metadata?.content ? <img src={ctx.node.metadata.content} alt={ctx.node.title} className="max-h-36 w-full object-contain" /> : null}
        {isUpload ? <label className="cursor-pointer underline" onMouseDown={(event) => event.stopPropagation()}><input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" disabled={busy} onChange={(event) => void upload(event.target.files?.[0])} />{busy ? "保存中…" : sourceId ? "更换商品原图" : "选择商品原图"}</label> : null}
        {isResult ? <><span>来源任务：{ctx.node.metadata?.workflowTaskId?.slice(0, 12)}</span>
            <span>参考图：{ctx.node.metadata?.workflowReferenceIds?.map((id) => id.slice(0, 8)).join("、") || "无"}</span>
            <span>模型：{ctx.node.metadata?.workflowModel || "未知"} · 费用未知</span>
            {ctx.node.metadata?.workflowInferred ? <span>推断视角，未核实结构</span> : null}
            {ctx.node.metadata?.workflowPrompt ? <span title={ctx.node.metadata.workflowPrompt}>提示词：{ctx.node.metadata.workflowPrompt.slice(0, 48)}</span> : null}
            <span>结果：{ctx.node.metadata?.workflowDecision || "待挑选"}</span>
            <div className="flex gap-3">{(["采用", "废图"] as const).map((decision) => <button key={decision} type="button" className="underline" onMouseDown={(event) => event.stopPropagation()} onClick={() => void (async () => {
                try { await studioApi.workflowReview(await studioId(), ctx.node.metadata!.workflowTaskId!, sourceId!, decision); ctx.updateMetadata({ workflowDecision: decision }); }
                catch (e) { setError(errorText(e)); }
            })()}>{decision}</button>)}</div></> : null}
        {!isUpload && !isResult ? <><span>{versionId ? `已确认版本 ${versionId.slice(0, 12)}` : "尚无确认输出"}</span>
            <button type="button" className="self-start underline" onMouseDown={(event) => event.stopPropagation()} onClick={() => ctx.openPanel()}>查看输入 / 编辑 / 生成</button></> : null}
        {error ? <span className="text-red-500">{error}</span> : null}
    </div>;
}

function WorkflowPanel({ ctx, onClose }: { ctx: CanvasNodeContext; onClose: () => void }) {
    const [project, setProject] = useState<StudioProject | null>(null);
    const [offer, setOffer] = useState<StudioQuote | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [draft, setDraft] = useState(ctx.node.metadata?.workflowDraft || "");
    const [view, setView] = useState(ctx.node.metadata?.workflowView || "正面");
    const [ratio, setRatio] = useState(ctx.node.metadata?.workflowRatio || "1:1");
    const [fields, setFields] = useState<WorkflowField[]>([]);
    const [candidateId, setCandidateId] = useState<string | undefined>();
    const [resolvingTaskId, setResolvingTaskId] = useState<string | null>(null);
    const [resolutionNote, setResolutionNote] = useState("");
    const isDetails = ctx.node.type === workflowKinds.details;
    const isPrompt = ctx.node.type === workflowKinds.prompt;
    const isText = isDetails || isPrompt;
    const load = useCallback(async () => {
        const id = await studioId();
        const next = await studioApi.get(id);
        setProject(next);
        await materializeResults(ctx, id, next);
    }, [ctx]);
    useEffect(() => { void load().catch((e) => setError(errorText(e))); }, [load]);
    const taskList = (project?.tasks || []).filter((task) => task.node_id === ctx.node.id).slice().reverse();
    const versions = (project?.workflow_versions || []).filter((v) => v.node_id === ctx.node.id);
    const inputEdges = ctx.getConnections().filter((edge) => edge.toNodeId === ctx.node.id);
    const patchField = (index: number, patch: Partial<WorkflowField>) => setFields((current) => current.map((item, i) => i === index ? { ...item, ...patch } : item));
    const guarded = async (action: () => Promise<void>) => {
        setBusy(true); setError("");
        try { await action(); await load(); } catch (e) { setError(errorText(e)); }
        finally { setBusy(false); }
    };
    const currentGraph = () => {
        const g = graph(ctx);
        const node = g.nodes.find((item) => item.id === ctx.node.id);
        if (node) { node.draft = draft; node.view = view; node.ratio = ratio; }
        return g;
    };
    const quote = () => guarded(async () => {
        const next = await studioApi.workflowQuote(await studioId(), ctx.node.id, currentGraph());
        setOffer(next);
    });
    const run = () => guarded(async () => {
        if (!offer) throw new Error("请先核对输入");
        await studioApi.workflowRun(await studioId(), ctx.node.id, currentGraph(), offer.fingerprint, crypto.randomUUID());
        setOffer(null);
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
        <div className="mb-3 text-xs" style={{ color: accent }}>模型费用：{offer?.estimate == null ? "未知" : `${offer.estimate} ${offer.currency || ""}`}。只有点击“生成候选 / 生成图片”才提交任务。</div>
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
                <b>候选 {task.id.slice(0, 8)}</b>{isDetails && !task.candidates?.length ? <p>模型未识别出可用字段，可手写后确认。</p> : null}{task.candidates?.map((field, index) => <div key={index} className="flex justify-between gap-2"><span>{field.field}：{field.value}</span><button type="button" className="underline" onClick={() => setFields((current) => [...current, { ...field, candidate_task_id: task.id }])}>选取</button></div>)}
                {isPrompt && task.candidate_text ? <><p className="whitespace-pre-wrap">{task.candidate_text}</p><button type="button" className="underline" onClick={() => { setDraft(task.candidate_text!); setCandidateId(task.id); ctx.updateMetadata({ workflowDraft: task.candidate_text }); setOffer(null); }}>选此整份</button></> : null}
            </div>)}</div>
            <button type="button" className="mr-4 underline" disabled={busy} onClick={() => void guarded(async () => { const health = await studioApi.health(); if (!health.deepseek) throw new Error("DeepSeek 未配置，仍可手写确认"); await studioApi.workflowRun(await studioId(), ctx.node.id, currentGraph(), (await studioApi.workflowQuote(await studioId(), ctx.node.id, currentGraph())).fingerprint, crypto.randomUUID()); })}>生成候选 · DeepSeek</button>
            <button type="button" className="underline" disabled={busy} onClick={() => void confirm()}>确认新版本</button>
            {versions.length ? <p className="mt-2 text-xs">已确认 {versions.length} 版；当前输出 {ctx.node.metadata?.workflowVersionId?.slice(0, 8)}</p> : null}
        </> : <>
            {ctx.node.type === workflowKinds.views ? <label>视角 <select className="ml-2 bg-transparent" value={view} onChange={(event) => { setView(event.target.value); ctx.updateMetadata({ workflowView: event.target.value }); setOffer(null); }}><option>正面</option><option>侧面</option><option>背面</option></select></label> : null}
            {ctx.node.type === workflowKinds.generate ? <label>比例 <select className="ml-2 bg-transparent" value={ratio} onChange={(event) => { setRatio(event.target.value); ctx.updateMetadata({ workflowRatio: event.target.value }); setOffer(null); }}>{["1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"].map((r) => <option key={r}>{r}</option>)}</select></label> : null}
            <div className="mt-3 flex gap-4"><button type="button" className="underline" disabled={busy} onClick={() => void quote()}>核对输入与费用</button><button type="button" className="underline" disabled={busy || !offer} onClick={() => void run()}>生成图片</button></div>
            {offer ? <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(offer.input_snapshot, null, 2)}</pre> : null}
        </>}
        {taskList.length ? <div className="mt-4 border-t pt-2"><b>任务</b>{taskList.map((task) => <div key={task.id} className="mt-1 text-xs">{task.id.slice(0, 8)} · {task.status} · {task.remote_id ? `远端 ${task.remote_id.slice(0, 12)}` : "无远端 ID"}
            {task.error ? ` · ${task.error}` : ""} {task.provider === "SeeAny" && task.remote_id && task.status !== "完成" ? <button type="button" className="underline" disabled={busy} onClick={() => void guarded(async () => { await studioApi.sync(await studioId(), task.id); })}>同步</button> : null}
            {task.status === "待核对" && !task.remote_id ? <><button type="button" className="ml-2 underline" disabled={busy} onClick={() => { setResolvingTaskId(task.id); setResolutionNote(""); }}>记录远端核对</button>{resolvingTaskId === task.id ? <div className="mt-1"><input className="w-full rounded border bg-transparent px-1" placeholder="填写供应商后台确认无任务的依据" value={resolutionNote} onChange={(event) => setResolutionNote(event.target.value)} /><button type="button" className="mt-1 underline" disabled={busy || !resolutionNote.trim()} onClick={() => void guarded(async () => { await studioApi.resolveUnidentifiedTask(await studioId(), task.id, resolutionNote); setResolvingTaskId(null); setResolutionNote(""); })}>确认远端无任务</button></div> : null}</> : null}</div>)}</div> : null}
        {error ? <p className="mt-2 text-red-500">{error}</p> : null}
    </div>;
}

const specs: Array<[string, string, React.ReactNode, string]> = [
    [workflowKinds.upload, "上传商品图", <FileImage className="size-5" />, "保存本机原图并输出图片资产"],
    [workflowKinds.details, "生成商品详细信息", <FileText className="size-5" />, "从相连原图生成逐项候选并确认"],
    [workflowKinds.views, "生成白底三视图", <Images className="size-5" />, "从相连原图生成指定视角"],
    [workflowKinds.prompt, "提示词", <MessageSquareText className="size-5" />, "手写或生成后确认版本"],
    [workflowKinds.generate, "生成商品图", <Sparkles className="size-5" />, "读取相连图片和确认提示词"],
    [workflowKinds.result, "商品图结果", <FileImage className="size-5" />, "独立生成结果"],
];
let registered = false;
export function registerCommerceWorkflowNodes() {
    if (registered) return;
    registered = true;
    registerNodeDefinitions(specs.map(([type, title, icon, description]): CanvasNodeDefinition => ({
        type, title, icon, description, defaultSize: { width: 290, height: 200 },
        defaultMetadata: type === workflowKinds.views ? { workflowView: "正面" } : type === workflowKinds.generate ? { workflowRatio: "1:1" } : {},
        showInCreateMenu: type !== workflowKinds.result, hasSourceHandle: type !== workflowKinds.generate,
        hidePanel: type === workflowKinds.upload || type === workflowKinds.result,
        Content: WorkflowContent, Panel: type === workflowKinds.upload || type === workflowKinds.result ? undefined : WorkflowPanel,
    })), "commerce-workflow");
}
