import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";

export const workflowKinds = {
    upload: "commerce:upload", details: "commerce:details", views: "commerce:views",
    prompt: "commerce:prompt", generate: "commerce:generate", result: "commerce:result",
} as const;

type Port = NonNullable<CanvasConnection["targetPort"]>;
const output: Record<string, string> = {
    [workflowKinds.upload]: "image", [workflowKinds.details]: "product_details",
    [workflowKinds.views]: "image", [workflowKinds.prompt]: "prompt",
    [workflowKinds.generate]: "image", [workflowKinds.result]: "image",
};
const input: Record<string, Partial<Record<Port, { kind: string; multiple: boolean }>>> = {
    [workflowKinds.details]: { image: { kind: "image", multiple: true } },
    [workflowKinds.views]: { image: { kind: "image", multiple: true }, product_details: { kind: "product_details", multiple: false } },
    [workflowKinds.prompt]: { image: { kind: "image", multiple: true }, product_details: { kind: "product_details", multiple: false } },
    [workflowKinds.generate]: { image: { kind: "image", multiple: true }, prompt: { kind: "prompt", multiple: false }, product_details: { kind: "product_details", multiple: false } },
    [workflowKinds.result]: { image: { kind: "image", multiple: false } },
};

export function isWorkflowNode(node?: CanvasNodeData) {
    return Boolean(node && output[node.type]);
}

export function workflowConnection(from: CanvasNodeData, to: CanvasNodeData, connections: CanvasConnection[]): { port: Port } | { error: string } {
    if (!isWorkflowNode(from) || !isWorkflowNode(to)) return { error: "业务节点只能连接兼容的业务端口" };
    const port = Object.entries(input[to.type] || {}).find(([, spec]) => spec.kind === output[from.type])?.[0] as Port | undefined;
    if (!port) return { error: "节点端口类型不兼容" };
    if (!input[to.type][port]?.multiple && connections.some((edge) => edge.toNodeId === to.id && edge.targetPort === port)) return { error: `${port} 端口已有输入，请先断开原连线` };
    const next = new Map<string, string[]>();
    for (const edge of connections) next.set(edge.fromNodeId, [...(next.get(edge.fromNodeId) || []), edge.toNodeId]);
    next.set(from.id, [...(next.get(from.id) || []), to.id]);
    const visited = new Set<string>();
    const stack = [to.id];
    while (stack.length) {
        const id = stack.pop()!;
        if (id === from.id) return { error: "画布连线不能形成循环" };
        if (visited.has(id)) continue;
        visited.add(id);
        stack.push(...(next.get(id) || []));
    }
    return { port };
}

export function workflowGraph(nodes: CanvasNodeData[], connections: CanvasConnection[], targetId?: string) {
    const reachable = new Set<string>(targetId ? [targetId] : nodes.map((node) => node.id));
    if (targetId) {
        const stack = [targetId];
        while (stack.length) {
            const child = stack.pop()!;
            for (const edge of connections.filter((item) => item.toNodeId === child)) if (!reachable.has(edge.fromNodeId)) {
                reachable.add(edge.fromNodeId);
                stack.push(edge.fromNodeId);
            }
        }
    }
    const selected = nodes.filter((node) => reachable.has(node.id) && isWorkflowNode(node));
    const ids = new Set(selected.map((node) => node.id));
    return {
        nodes: selected.map((node) => ({ id: node.id, kind: node.type,
            source_id: node.metadata?.workflowSourceId, draft: node.metadata?.workflowDraft,
            view: node.metadata?.workflowView, ratio: node.metadata?.workflowRatio })),
        edges: connections.filter((edge) => ids.has(edge.fromNodeId) && ids.has(edge.toNodeId) &&
            selected.find((node) => node.id === edge.toNodeId)?.type !== workflowKinds.result)
            .map((edge) => ({ id: edge.id, fromNodeId: edge.fromNodeId, toNodeId: edge.toNodeId,
                targetPort: edge.targetPort, selectedVersionId: edge.selectedVersionId })),
    };
}
