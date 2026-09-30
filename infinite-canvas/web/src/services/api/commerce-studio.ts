const BASE = "http://127.0.0.1:8766/api";

export type StudioSource = { id: string; name: string; mime: string; bytes: number; extracted_text?: string; parse_status?: string; origin?: string; import_prompt?: string; candidate_group?: number; view_label?: string; reference_ids?: string[]; gallery_plan_id?: string; gallery_item_id?: string; prompt_version_id?: string };
export type StudioFact = { id: string; field: string; value: string; source_id?: string; status: "待核实" | "已知事实" | "创意假设" };
export type InferredDetail = { id: string; text: string; status: "待核实" | "已核实"; evidence_source_id: string | null };
export type ImageObservation = { id: string; task_id: string; original_id: string; candidate_id: string; brief_id: string; fields: Record<string, string>; corrections: Array<{ id: string; field: string; text: string; at: string }>; created: string };
export type GalleryItem = { id: string; kind: string; prompt: string; ratio: string; title: string; subtitle: string; x: number; y: number };
export type GalleryPlan = { id: string; parent_id: string | null; brief_id: string; master_id: string; origin: string; items: GalleryItem[]; created: string };
export type StudioCost = { id: string; task_id: string; provider: string; stage: string; node_id: string | null; purpose: string; model: string | null; estimate: number | null; actual: number | null; currency: string | null; pricing_source: string; status: string; submitted_at: string; settled_at: string | null; receipt_source?: string; settlement_method?: string; revisions?: Array<{ actual: number | null; currency: string | null; receipt_source?: string }> };
export type JevObservation = { id: string; task_id: string; brief_id: string; master_id: string; direction_ids: string[]; choice_id: string; confidence: number; probabilities: Record<string, number>; model: string; usage?: { input_tokens: number; output_tokens: number }; comparisons: Array<{ direction_id: string; agrees: boolean; at: string }>; created: string };
export type PromptFields = { product: string; scene: string; composition: string; lighting: string; negative: string; ratio: string };
export type PromptVersion = { id: string; parent_id: string | null; brief_id: string; master_id: string; fields: PromptFields; text: string; origin: string; changed_fields: string[]; created: string };
export type FlovaPendingAction = { action_id?: string; type?: string; message?: string; blocking?: boolean; resume_message_id?: string; payload?: unknown; action_url?: string; options?: Array<{ id: string; effect: "resume" | "open_url" | "none"; label?: string }> };
export type StudioProject = {
    id: string;
    restored_from?: string;
    name: string;
    legacy_import?: { id: string; imported_at: string; review_required: string; archive: unknown };
    sources: StudioSource[];
    facts: StudioFact[];
    image_observations: ImageObservation[];
    jev_observations: JevObservation[];
    brief_versions: Array<{ id: string; facts: StudioFact[] }>;
    master_versions: Array<{ id: string; asset_ids: string[]; inferred_details: Array<InferredDetail | string> }>;
    directions: Array<{ id: string; brief_id: string; master_id: string; title: string; audience: string; opening: string; selling_point: string; ending: string }>;
    direction_approval: string | null;
    script_versions: Array<{ id: string; text: string; direction_id: string; brief_id: string; master_id: string }>;
    script_approval: string | null;
    storyboard_versions: Array<{ id: string; shots: Array<{ id: string; visual: string; duration: number; reference_asset_id: string; caption: string; detail_ids?: string[] }> }>;
    storyboard_approval: string | null;
    gallery_versions: GalleryPlan[];
    gallery_approval: string | null;
    gallery_choices: Record<string, string>;
    gallery_reviews: Array<{ id: string; item_id: string; source_id: string; decision: string; reason: string }>;
    prompt_versions: PromptVersion[];
    prompt_adoption: { version_id: string; source_id: string; review_id: string } | null;
    prompt_reviews: Array<{ id: string; version_id: string; source_id: string; decision: string; reason: string }>;
    canvas_preview_reviews: Array<{ id: string; task_id: string; source_id: string; node_id: string; decision: string; reason: string }>;
    canvas_preview_adoption: Record<string, string>;
    video_approval: { task_id: string; stream_chat_id: string; approved_at: string } | null;
    shot_approvals: Record<string, { task_id: string; stream_chat_id: string; storyboard_id: string; approved_at: string }>;
    deliverables: Array<{ id: string; kind: string; task_id?: string; edit_id?: string; resource_id?: string; name: string; bytes: number; sha256: string }>;
    video_edits: Array<{ id: string; parent_id: string | null; clips: Array<{ id: string; source_id: string; start: number; end: number; speed: number; caption: string; preview_duration: number }>; preview_duration: number }>;
    costs: StudioCost[];
    cost_target: { amount: number; currency: string } | null;
    external: { flova_project_id: string; flova_project_url: string };
    tasks: Array<{ id: string; provider: string; kind?: string; node_id?: string; status: string; remote_id?: string | null; estimate: number | null; actual: number | null; candidate_group?: number; view_label?: string; asset_ids?: string[]; source_id?: string; input_snapshot?: { item?: { id: string }; version_id?: string; shot_id?: string; storyboard_id?: string; config_node_id?: string; brief_id?: string; master_id?: string; prompt?: string; reference_node_id?: string; connection_ids?: string[]; reference_sha256?: string }; error?: string; interrupted_at?: string; manual_resolution?: { outcome: string; note: string; at: string }; pending_actions?: FlovaPendingAction[] }>;
    chat: Array<{ id: string; prompt: string; reply: string; brief_id?: string; created: string }>;
};

export type MasterRequest = { group: number; view_label: string; views: string; source_id: string };
export type StudioQuote = { fingerprint: string; estimate: number | null; currency?: string | null; pricing_source: string; input_snapshot: unknown };
export type PreviewRequest = { canvas_project_id: string; config_node_id: string; reference_node_id: string; connection_ids: string[]; prompt: string; ratio: string; reference_data_url: string };
export type FlovaCanvasContext = { canvas_project_id: string; config_node_id: string; connection_ids: string[]; prompt: string; reference_images: Array<{ node_id: string; sha256: string }>; reference_media: Array<{ node_id: string; source_id: string; sha256: string; kind: "video" | "audio" }> };

async function request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${BASE}${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "本地服务请求失败");
    return result as T;
}

export const studioApi = {
    health: () => request<{ ok: boolean; deepseek: boolean; seeany: boolean; flova: boolean; jev: boolean }>("/health"),
    legacyProjects: () => request<Array<{ id: string; name: string; sources: number; facts: number }>>("/legacy/projects"),
    legacyImport: (id: string) => request<StudioProject>("/legacy/import", { id }),
    search: (query: string) => request<Array<{ project_id: string; project_name: string; kind: string; text: string; updated: string }>>(`/search?q=${encodeURIComponent(query)}`),
    create: (name: string) => request<StudioProject>("/projects", { name }),
    get: (id: string) => request<StudioProject>(`/projects/${id}`),
    delete: (id: string) => request<{ deleted_id: string }>(`/projects/${id}/delete`, {}),
    source: (id: string, file: File) =>
        new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(",")[1]);
            reader.onerror = () => reject(new Error("无法读取本机文件"));
            reader.readAsDataURL(file);
        }).then((base64) => {
            const extension = file.name.toLowerCase().split(".").at(-1) || "";
            const fallback: Record<string, string> = { txt: "text/plain", pdf: "application/pdf", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", gif: "image/gif" };
            return request<StudioSource>(`/projects/${id}/sources`, { name: file.name, mime: file.type || fallback[extension] || "application/octet-stream", base64 });
        }),
    fact: (id: string, body: { field: string; value: string; source_id?: string; status: StudioFact["status"] }) => request<StudioFact>(`/projects/${id}/facts`, body),
    reviewFact: (id: string, factId: string, status: StudioFact["status"]) => request<StudioFact>(`/projects/${id}/facts/${factId}/review`, { status }),
    factsQuote: (id: string, sourceId: string) => request<StudioQuote>(`/projects/${id}/facts/quote`, { source_id: sourceId }),
    factsRun: (id: string, sourceId: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/facts/run`, { source_id: sourceId, approved_fingerprint, request_id }),
    observationQuote: (id: string, original_id: string, candidate_id: string) => request<StudioQuote>(`/projects/${id}/observations/quote`, { original_id, candidate_id }),
    observationRun: (id: string, original_id: string, candidate_id: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/observations/run`, { original_id, candidate_id, approved_fingerprint, request_id }),
    observationCorrect: (id: string, observationId: string, field: string, text: string) => request(`/projects/${id}/observations/${observationId}/correct`, { field, text }),
    brief: (id: string, fact_ids: string[]) => request(`/projects/${id}/briefs/confirm`, { fact_ids }),
    master: (id: string, asset_ids: string[], inferred_details: string[]) => request(`/projects/${id}/masters/confirm`, { asset_ids, inferred_details }),
    verifyMasterDetail: (id: string, detail_id: string, evidence_source_id: string) => request(`/projects/${id}/masters/verify-detail`, { detail_id, evidence_source_id }),
    masterQuote: (id: string, body: MasterRequest) => request<StudioQuote>(`/projects/${id}/master-candidates/quote`, body),
    masterRun: (id: string, body: MasterRequest & { approved_fingerprint: string; request_id: string }) => request(`/projects/${id}/master-candidates/run`, body),
    sync: (id: string, taskId: string) => request(`/projects/${id}/tasks/${taskId}/sync`, {}),
    chatQuote: (id: string, prompt: string) => request<StudioQuote>(`/projects/${id}/chat/quote`, { prompt }),
    chatRun: (id: string, prompt: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/chat/run`, { prompt, approved_fingerprint, request_id }),
    previewQuote: (id: string, body: PreviewRequest) => request<StudioQuote>(`/projects/${id}/preview/quote`, body),
    previewRun: (id: string, body: PreviewRequest & { approved_fingerprint: string; request_id: string }) => request(`/projects/${id}/preview/run`, body),
    previewReview: (id: string, task_id: string, source_id: string, decision: "采用" | "废图", reason = "") => request(`/projects/${id}/preview/review`, { task_id, source_id, decision, reason }),
    sourceData: (id: string, sourceId: string) => request<{ name: string; mime: string; data_url: string }>(`/projects/${id}/sources/${sourceId}`),
    directionsQuote: (id: string) => request<StudioQuote>(`/projects/${id}/directions/quote`, {}),
    directionsRun: (id: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/directions/run`, { approved_fingerprint, request_id }),
    jevDirectionsQuote: (id: string) => request<StudioQuote>(`/projects/${id}/jev/directions/quote`, {}),
    jevDirectionsRun: (id: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/jev/directions/run`, { approved_fingerprint, request_id }),
    approveDirection: (id: string, direction_id: string) => request(`/projects/${id}/directions/approve`, { direction_id }),
    script: (id: string, text: string) => request(`/projects/${id}/scripts`, { text }),
    approveScript: (id: string, script_id: string) => request(`/projects/${id}/scripts/approve`, { script_id }),
    storyboard: (id: string, shots: Array<{ visual: string; duration: number; reference_asset_id: string; caption: string; detail_ids: string[] }>) => request(`/projects/${id}/storyboards`, { shots }),
    approveStoryboard: (id: string, storyboard_id: string) => request(`/projects/${id}/storyboards/approve`, { storyboard_id }),
    flovaCreate: (id: string) => request(`/projects/${id}/flova/create`, {}),
    flovaAttach: (id: string, project_id: string) => request(`/projects/${id}/flova/attach`, { project_id }),
    flovaQuote: (id: string, canvas_context?: FlovaCanvasContext) => request<StudioQuote>(`/projects/${id}/flova/quote`, { canvas_context }),
    flovaShotQuote: (id: string, shot_id: string) => request<StudioQuote>(`/projects/${id}/flova/shots/quote`, { shot_id }),
    flovaShotRun: (id: string, shot_id: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/flova/shots/run`, { shot_id, approved_fingerprint, request_id }),
    flovaShotApprove: (id: string, task_id: string) => request(`/projects/${id}/flova/shots/approve`, { task_id }),
    flovaShotSequenceApprove: (id: string) => request(`/projects/${id}/flova/shots/approve-sequence`, {}),
    flovaRun: (id: string, approved_fingerprint: string, request_id: string, canvas_context?: FlovaCanvasContext) => request(`/projects/${id}/flova/run`, { approved_fingerprint, request_id, canvas_context }),
    flovaMedia: async (id: string, nodeId: string, kind: "video" | "audio", blob: Blob, mime: string) => {
        const response = await fetch(`${BASE}/projects/${id}/flova/media?node_id=${encodeURIComponent(nodeId)}&kind=${kind}`, { method: "POST", headers: { "Content-Type": mime }, body: blob });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "画布参考媒体上传失败");
        return result as { id: string; sha256: string; mime: string; bytes: number };
    },
    flovaRecover: (id: string, taskId: string) => request(`/projects/${id}/flova/${taskId}/recover`, {}),
    flovaResumeAction: (id: string, task_id: string, action_id: string, option_id: string) => request(`/projects/${id}/flova/actions/resume`, { task_id, action_id, option_id }),
    flovaResources: (id: string) => request<{ items: Array<{ resource_id: string; name?: string; media_type?: string; status?: string }>; unparsed: boolean }>(`/projects/${id}/flova/resources`, {}),
    flovaPullResource: (id: string, resource_id: string) => request(`/projects/${id}/flova/resources/pull`, { resource_id }),
    flovaApprove: (id: string, task_id: string) => request(`/projects/${id}/flova/approve`, { task_id }),
    flovaExportQuote: (id: string) => request<StudioQuote>(`/projects/${id}/flova/export/quote`, {}),
    flovaExportRun: (id: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/flova/export/run`, { approved_fingerprint, request_id }),
    flovaExportRecover: (id: string, taskId: string) => request(`/projects/${id}/flova/export/${taskId}/recover`, {}),
    videoBlob: async (id: string, deliverableId: string) => {
        const response = await fetch(`${BASE}/projects/${id}/deliverables/${deliverableId}`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || "无法读取本地视频");
        }
        return response.blob();
    },
    videoProbe: (id: string, source_id: string) => request<{ duration: number; audio: boolean }>(`/projects/${id}/finishing/probe`, { source_id }),
    videoEditSave: (id: string, clips: Array<{ source_id: string; start: number; end: number; speed: number; caption: string }>, parent_id: string | null) => request(`/projects/${id}/finishing/edits`, { clips, parent_id }),
    videoEditRender: (id: string, edit_id: string) => request(`/projects/${id}/finishing/render`, { edit_id }),
    settleCost: (id: string, taskId: string, amount: number, currency: string, receipt: string) => request(`/projects/${id}/costs/${taskId}/settle`, { amount, currency, receipt }),
    setCostTarget: (id: string, amount: number | null, currency: string) => request(`/projects/${id}/costs/target`, { amount, currency }),
    resolveUnidentifiedTask: (id: string, taskId: string, note: string) => request(`/projects/${id}/tasks/${taskId}/resolve-unidentified`, { note }),
    galleryDefault: (id: string) => request<GalleryPlan>(`/projects/${id}/gallery/default`, {}),
    gallerySave: (id: string, items: Array<Omit<GalleryItem, "id"> | GalleryItem>, parent_id: string | null) => request<GalleryPlan>(`/projects/${id}/gallery/plans`, { items, parent_id }),
    galleryApprove: (id: string, plan_id: string) => request<GalleryPlan>(`/projects/${id}/gallery/plans/approve`, { plan_id }),
    galleryPlanQuote: (id: string, requirement: string) => request<StudioQuote>(`/projects/${id}/gallery/planning/quote`, { requirement }),
    galleryPlanRun: (id: string, requirement: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/gallery/planning/run`, { requirement, approved_fingerprint, request_id }),
    galleryImageQuote: (id: string, item_id: string) => request<StudioQuote>(`/projects/${id}/gallery/images/quote`, { item_id }),
    galleryImageRun: (id: string, item_id: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/gallery/images/run`, { item_id, approved_fingerprint, request_id }),
    galleryReview: (id: string, item_id: string, source_id: string, decision: "采用" | "废图", reason = "") => request(`/projects/${id}/gallery/images/review`, { item_id, source_id, decision, reason }),
    galleryExport: async (id: string) => {
        const response = await fetch(`${BASE}/projects/${id}/gallery/export`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || "套图导出失败");
        }
        return response.blob();
    },
    deliveryBlob: async (id: string, videoId: string) => {
        const response = await fetch(`${BASE}/projects/${id}/delivery/export/${videoId}`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || "交付包导出失败");
        }
        return response.blob();
    },
    backupBlob: async (id: string) => {
        const response = await fetch(`${BASE}/projects/${id}/backup`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || "商品项目备份失败");
        }
        return response.blob();
    },
    restoreBlob: async (archive: Blob) => {
        const response = await fetch(`${BASE}/restore`, { method: "POST", headers: { "Content-Type": "application/zip" }, body: archive });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "商品项目恢复失败");
        return result as StudioProject;
    },
    promptSave: (id: string, fields: PromptFields, parent_id: string | null) => request<PromptVersion>(`/projects/${id}/prompts/versions`, { fields, parent_id }),
    promptChangeQuote: (id: string, parent_id: string, changed_fields: string[], instruction: string) => request<StudioQuote>(`/projects/${id}/prompts/changes/quote`, { parent_id, changed_fields, instruction }),
    promptChangeRun: (id: string, parent_id: string, changed_fields: string[], instruction: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/prompts/changes/run`, { parent_id, changed_fields, instruction, approved_fingerprint, request_id }),
    promptPreviewQuote: (id: string, version_id: string, reference_id: string) => request<StudioQuote>(`/projects/${id}/prompts/preview/quote`, { version_id, reference_id }),
    promptPreviewRun: (id: string, version_id: string, reference_id: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/prompts/preview/run`, { version_id, reference_id, approved_fingerprint, request_id }),
    promptImport: (id: string, file: File, version_id: string, reference_id: string, external_prompt: string) => new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("无法读取本机图片"));
        reader.readAsDataURL(file);
    }).then((base64) => request(`/projects/${id}/prompts/preview/import`, { name: file.name, mime: file.type, base64, version_id, reference_id, external_prompt })),
    promptReview: (id: string, version_id: string, source_id: string, decision: "采用" | "废图", reason = "") => request(`/projects/${id}/prompts/preview/review`, { version_id, source_id, decision, reason }),
};
