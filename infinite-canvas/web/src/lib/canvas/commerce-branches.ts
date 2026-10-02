import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";

export type CommerceBranchState = {
    projectId: string;
    versions: Array<{ id: string; parent_id: string | null }>;
    adoption: { version_id: string; source_id: string } | null;
};

export function hiddenCommerceBranchNodeIds(nodes: CanvasNodeData[], connections: CanvasConnection[], branch: CommerceBranchState | null) {
    const hidden = new Set<string>();
    if (!branch?.adoption) return hidden;
    const versions = new Map(branch.versions.map((version) => [version.id, version]));
    const mainline = new Set<string>();
    for (let id: string | null | undefined = branch.adoption.version_id; id && !mainline.has(id); id = versions.get(id)?.parent_id) mainline.add(id);
    const pinned = new Set<string>();
    const adopted = new Set<string>();
    for (const node of nodes) {
        const prompt = node.metadata?.commerceSource;
        const image = node.metadata?.commercePromptImage;
        if (prompt?.projectId === branch.projectId && prompt.kind === "prompt") {
            (mainline.has(prompt.versionId) ? pinned : hidden).add(node.id);
            if (prompt.versionId === branch.adoption.version_id) adopted.add(node.id);
        }
        if (image?.projectId === branch.projectId) {
            (image.sourceId === branch.adoption.source_id && mainline.has(image.versionId) ? pinned : hidden).add(node.id);
            if (image.sourceId === branch.adoption.source_id) adopted.add(node.id);
        }
    }
    for (let changed = true; changed;) {
        changed = false;
        for (const connection of connections) {
            if (adopted.has(connection.fromNodeId) && !adopted.has(connection.toNodeId) && !hidden.has(connection.toNodeId)) {
                adopted.add(connection.toNodeId);
                changed = true;
            }
        }
    }
    for (let changed = true; changed;) {
        changed = false;
        for (const connection of connections) {
            if (hidden.has(connection.fromNodeId) && !hidden.has(connection.toNodeId) && !pinned.has(connection.toNodeId) && !adopted.has(connection.toNodeId)) {
                hidden.add(connection.toNodeId);
                changed = true;
            }
        }
    }
    return hidden;
}
