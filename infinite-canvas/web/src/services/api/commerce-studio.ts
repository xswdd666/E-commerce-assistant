const BASE = "http://127.0.0.1:8766/api";

export type StudioSource = { id: string; name: string; mime: string; bytes: number; parse_status?: string; origin?: string; candidate_group?: number; view_label?: string; reference_ids?: string[] };
export type StudioFact = { id: string; field: string; value: string; source_id?: string; status: "待核实" | "已知事实" | "创意假设" };
export type StudioProject = {
    id: string;
    name: string;
    sources: StudioSource[];
    facts: StudioFact[];
    brief_versions: Array<{ id: string; facts: StudioFact[] }>;
    master_versions: Array<{ id: string; asset_ids: string[]; inferred_details: string[] }>;
    directions: Array<{ id: string; brief_id: string; master_id: string; title: string; audience: string; opening: string; selling_point: string; ending: string }>;
    direction_approval: string | null;
    script_versions: Array<{ id: string; text: string; direction_id: string; brief_id: string; master_id: string }>;
    script_approval: string | null;
    storyboard_versions: Array<{ id: string; shots: Array<{ id: string; visual: string; duration: number; reference_asset_id: string; caption: string }> }>;
    storyboard_approval: string | null;
    external: { flova_project_id: string; flova_project_url: string };
    tasks: Array<{ id: string; provider: string; kind?: string; status: string; estimate: number | null; actual: number | null; candidate_group?: number; view_label?: string; asset_ids?: string[]; source_id?: string; pending_actions?: Array<{ type?: string; message?: string; blocking?: boolean }> }>;
    chat: Array<{ id: string; prompt: string; reply: string; brief_id?: string; created: string }>;
};

export type MasterRequest = { group: number; view_label: string; views: string; source_id: string };
export type StudioQuote = { fingerprint: string; estimate: number | null; pricing_source: string; input_snapshot: unknown };
export type PreviewRequest = { canvas_project_id: string; config_node_id: string; reference_node_id: string; connection_ids: string[]; prompt: string; ratio: string; reference_data_url: string };

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
    health: () => request<{ ok: boolean; deepseek: boolean; seeany: boolean; flova: boolean }>("/health"),
    create: (name: string) => request<StudioProject>("/projects", { name }),
    get: (id: string) => request<StudioProject>(`/projects/${id}`),
    source: (id: string, file: File) =>
        new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(",")[1]);
            reader.onerror = () => reject(new Error("无法读取本机文件"));
            reader.readAsDataURL(file);
        }).then((base64) => request<StudioSource>(`/projects/${id}/sources`, { name: file.name, mime: file.type || "application/octet-stream", base64 })),
    fact: (id: string, body: { field: string; value: string; source_id?: string; status: StudioFact["status"] }) => request<StudioFact>(`/projects/${id}/facts`, body),
    reviewFact: (id: string, factId: string, status: StudioFact["status"]) => request<StudioFact>(`/projects/${id}/facts/${factId}/review`, { status }),
    factsQuote: (id: string, sourceId: string) => request<StudioQuote>(`/projects/${id}/facts/quote`, { source_id: sourceId }),
    factsRun: (id: string, sourceId: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/facts/run`, { source_id: sourceId, approved_fingerprint, request_id }),
    brief: (id: string, fact_ids: string[]) => request(`/projects/${id}/briefs/confirm`, { fact_ids }),
    master: (id: string, asset_ids: string[]) => request(`/projects/${id}/masters/confirm`, { asset_ids, inferred_details: [] }),
    masterQuote: (id: string, body: MasterRequest) => request<StudioQuote>(`/projects/${id}/master-candidates/quote`, body),
    masterRun: (id: string, body: MasterRequest & { approved_fingerprint: string; request_id: string }) => request(`/projects/${id}/master-candidates/run`, body),
    sync: (id: string, taskId: string) => request(`/projects/${id}/tasks/${taskId}/sync`, {}),
    chatQuote: (id: string, prompt: string) => request<StudioQuote>(`/projects/${id}/chat/quote`, { prompt }),
    chatRun: (id: string, prompt: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/chat/run`, { prompt, approved_fingerprint, request_id }),
    previewQuote: (id: string, body: PreviewRequest) => request<StudioQuote>(`/projects/${id}/preview/quote`, body),
    previewRun: (id: string, body: PreviewRequest & { approved_fingerprint: string; request_id: string }) => request(`/projects/${id}/preview/run`, body),
    sourceData: (id: string, sourceId: string) => request<{ name: string; mime: string; data_url: string }>(`/projects/${id}/sources/${sourceId}`),
    directionsQuote: (id: string) => request<StudioQuote>(`/projects/${id}/directions/quote`, {}),
    directionsRun: (id: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/directions/run`, { approved_fingerprint, request_id }),
    approveDirection: (id: string, direction_id: string) => request(`/projects/${id}/directions/approve`, { direction_id }),
    script: (id: string, text: string) => request(`/projects/${id}/scripts`, { text }),
    approveScript: (id: string, script_id: string) => request(`/projects/${id}/scripts/approve`, { script_id }),
    storyboard: (id: string, shots: Array<{ visual: string; duration: number; reference_asset_id: string; caption: string }>) => request(`/projects/${id}/storyboards`, { shots }),
    approveStoryboard: (id: string, storyboard_id: string) => request(`/projects/${id}/storyboards/approve`, { storyboard_id }),
    flovaCreate: (id: string) => request(`/projects/${id}/flova/create`, {}),
    flovaAttach: (id: string, project_id: string) => request(`/projects/${id}/flova/attach`, { project_id }),
    flovaQuote: (id: string) => request<StudioQuote>(`/projects/${id}/flova/quote`, {}),
    flovaRun: (id: string, approved_fingerprint: string, request_id: string) => request(`/projects/${id}/flova/run`, { approved_fingerprint, request_id }),
    flovaRecover: (id: string, taskId: string) => request(`/projects/${id}/flova/${taskId}/recover`, {}),
};
