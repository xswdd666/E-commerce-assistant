import type { StudioProject } from "@/services/api/commerce-studio";

export type CommerceNodeStatus = "等待连接" | "等待" | "运行中" | "完成" | "需审核" | "失败";

export function commerceNodeStatus(nodeId: string, hasInputs: boolean, tasks: StudioProject["tasks"]): CommerceNodeStatus {
    const task = tasks.findLast((item) => item.node_id === nodeId || item.input_snapshot?.config_node_id === nodeId);
    if (!task) return hasInputs ? "等待" : "等待连接";
    if (task.status === "已排队") return "等待";
    if (task.status === "上传素材" || task.status === "远端运行中") return "运行中";
    if (task.status === "完成") return "完成";
    if (task.status === "失败" || task.status === "部分失败") return "失败";
    return "需审核";
}
