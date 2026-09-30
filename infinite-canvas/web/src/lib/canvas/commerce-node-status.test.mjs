import assert from "node:assert/strict";
import test from "node:test";

import { commerceNodeStatus, previewInputChanged } from "./commerce-node-status.ts";

test("commerce canvas status follows the latest task and connection state", () => {
    assert.equal(commerceNodeStatus("node", false, []), "等待连接");
    assert.equal(commerceNodeStatus("node", true, []), "等待");
    const statuses = ["已排队", "上传素材", "远端运行中", "待审核", "完成", "失败", "待核对"];
    const expected = ["等待", "运行中", "运行中", "需审核", "完成", "失败", "需审核"];
    statuses.forEach((status, index) => assert.equal(commerceNodeStatus("node", true, [{ id: String(index), provider: "SeeAny", status, node_id: "node" }]), expected[index]));
    assert.equal(commerceNodeStatus("node", true, [
        { id: "old", provider: "SeeAny", status: "失败", node_id: "node" },
        { id: "new", provider: "SeeAny", status: "远端运行中", input_snapshot: { config_node_id: "node" } },
    ]), "运行中");
    assert.equal(commerceNodeStatus("node", true, [{ id: "done", provider: "SeeAny", status: "完成", node_id: "node" }], true), "需重新核对");
});

test("preview fingerprint comparison catches changed inputs without depending on edge order", () => {
    const snapshot = { brief_id: "brief", master_id: "master", prompt: "原提示词", reference_node_id: "image", connection_ids: ["a", "b"], reference_sha256: "hash" };
    const current = { briefId: "brief", masterId: "master", prompt: "原提示词", referenceNodeId: "image", connectionIds: ["b", "a"], referenceSha256: "hash" };
    assert.equal(previewInputChanged(snapshot, current), false);
    assert.equal(previewInputChanged(snapshot, { ...current, prompt: "新提示词" }), true);
    assert.equal(previewInputChanged(snapshot, { ...current, referenceSha256: "other" }), true);
    assert.equal(previewInputChanged(snapshot, { ...current, connectionIds: ["a"] }), true);
});
