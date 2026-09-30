import type { StudioProject } from "@/services/api/commerce-studio";

export type CommerceNodeStatus = "等待连接" | "等待" | "运行中" | "完成" | "需审核" | "失败" | "需重新核对";

export function commerceNodeStatus(nodeId: string, hasInputs: boolean, tasks: StudioProject["tasks"], inputChanged = false): CommerceNodeStatus {
    const task = tasks.findLast((item) => item.node_id === nodeId || item.input_snapshot?.config_node_id === nodeId);
    if (!task) return hasInputs ? "等待" : "等待连接";
    if (inputChanged) return "需重新核对";
    if (task.status === "已排队") return "等待";
    if (task.status === "上传素材" || task.status === "远端运行中") return "运行中";
    if (task.status === "完成") return "完成";
    if (task.status === "失败" || task.status === "部分失败") return "失败";
    return "需审核";
}

export function previewInputChanged(snapshot: NonNullable<StudioProject["tasks"][number]["input_snapshot"]>, current: {
    briefId?: string; masterId?: string; prompt: string; referenceNodeId?: string; connectionIds: string[]; referenceSha256?: string;
}) {
    return snapshot.brief_id !== current.briefId || snapshot.master_id !== current.masterId || snapshot.prompt !== current.prompt ||
        snapshot.reference_node_id !== current.referenceNodeId ||
        JSON.stringify([...(snapshot.connection_ids || [])].sort()) !== JSON.stringify([...current.connectionIds].sort()) ||
        (current.referenceSha256 !== undefined && snapshot.reference_sha256 !== current.referenceSha256);
}
