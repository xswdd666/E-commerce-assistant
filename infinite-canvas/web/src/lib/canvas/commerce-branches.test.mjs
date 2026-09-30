import assert from "node:assert/strict";
import test from "node:test";

import { hiddenCommerceBranchNodeIds } from "./commerce-branches.ts";

const prompt = (id, versionId) => ({ id, metadata: { commerceSource: { projectId: "product", kind: "prompt", versionId } } });
const image = (id, sourceId, versionId) => ({ id, metadata: { commercePromptImage: { projectId: "product", sourceId, versionId } } });
const edge = (fromNodeId, toNodeId) => ({ id: `${fromNodeId}-${toNodeId}`, fromNodeId, toNodeId });

test("adopted prompt lineage remains visible while other branches and descendants fold", () => {
    const nodes = [prompt("root", "v1"), prompt("chosen", "v2"), prompt("other", "v3"), image("winner", "asset2", "v2"), image("loser", "asset3", "v3"), { id: "child" }, { id: "shared" }, { id: "unrelated" }];
    const connections = [edge("root", "chosen"), edge("root", "other"), edge("chosen", "winner"), edge("other", "loser"), edge("loser", "child"), edge("winner", "shared"), edge("loser", "shared")];
    const state = { projectId: "product", versions: [{ id: "v1", parent_id: null }, { id: "v2", parent_id: "v1" }, { id: "v3", parent_id: "v1" }], adoption: { version_id: "v2", source_id: "asset2" } };
    assert.deepEqual([...hiddenCommerceBranchNodeIds(nodes, connections, state)].sort(), ["child", "loser", "other"]);
    assert.deepEqual([...hiddenCommerceBranchNodeIds(nodes, connections, { ...state, adoption: { version_id: "v3", source_id: "asset3" } })].sort(), ["chosen", "winner"]);
    assert.equal(hiddenCommerceBranchNodeIds(nodes, connections, { ...state, adoption: null }).size, 0);
});
