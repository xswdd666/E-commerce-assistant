import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from commerce_studio.core import Store
from commerce_studio import backup, service, workflow


def png():
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
    return stream.getvalue()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.project = self.store.create("节点测试")
        self.source = self.store.add_source(self.project, "product.png", "image/png", base64.b64encode(png()).decode())

    def graph(self, prompt_version=None):
        nodes = [{"id": "upload", "kind": "commerce:upload", "source_id": self.source["id"]},
                 {"id": "details", "kind": "commerce:details"},
                 {"id": "prompt", "kind": "commerce:prompt", "draft": "白底商品图"},
                 {"id": "generate", "kind": "commerce:generate", "ratio": "1:1"}]
        edges = [{"id": "img", "fromNodeId": "upload", "toNodeId": "generate", "targetPort": "image"},
                 {"id": "text", "fromNodeId": "prompt", "toNodeId": "generate", "targetPort": "prompt",
                  "selectedVersionId": prompt_version}]
        return {"nodes": nodes, "edges": edges}

    def test_side_branch_has_no_global_brief_or_master_gate_and_pins_version(self):
        v1 = workflow.confirm(self.store, self.project, {"node_id": "prompt", "kind": "commerce:prompt", "text": "白底展示"})
        graph = self.graph(v1["id"])
        body = {"node_id": "generate", "graph": graph}
        offer = workflow.quote(self.store, self.project, body)
        self.assertEqual(offer["input_snapshot"]["inputs"][1]["version_id"], v1["id"])
        v2 = workflow.confirm(self.store, self.project, {"node_id": "prompt", "kind": "commerce:prompt", "text": "蓝色背景"})
        self.assertEqual(workflow.quote(self.store, self.project, body)["fingerprint"], offer["fingerprint"])
        graph["edges"][1]["selectedVersionId"] = v2["id"]
        self.assertNotEqual(workflow.quote(self.store, self.project, body)["fingerprint"], offer["fingerprint"])
        with patch.object(workflow, "provider_key", return_value="test"):
            with self.assertRaisesRegex(ValueError, "重新核对"):
                workflow.run(self.store, self.project, {**body, "approved_fingerprint": offer["fingerprint"], "request_id": "request-123"})

    def test_details_candidate_confirm_and_unconfirmed_block(self):
        graph = self.graph()
        graph["edges"] = [{"id": "source", "fromNodeId": "upload", "toNodeId": "details", "targetPort": "image"},
                          {"id": "facts", "fromNodeId": "details", "toNodeId": "generate", "targetPort": "product_details"}]
        with self.assertRaisesRegex(ValueError, "尚未选择已确认版本"):
            workflow.quote(self.store, self.project, {"node_id": "generate", "graph": graph})
        version = workflow.confirm(self.store, self.project, {"node_id": "details", "kind": "commerce:details",
            "fields": [{"field": "颜色", "value": "白色", "status": "已知事实", "source_id": self.source["id"]},
                       {"field": "背面", "value": "未知", "status": "待核实"}]})
        self.assertEqual(version["fields"][1]["status"], "待核实")
        graph["edges"][1]["selectedVersionId"] = version["id"]
        graph["nodes"].append({"id": "views", "kind": "commerce:views", "view": "正面"})
        graph["edges"] = [{"id": "source", "fromNodeId": "upload", "toNodeId": "views", "targetPort": "image"},
                          {"id": "facts", "fromNodeId": "details", "toNodeId": "views", "targetPort": "product_details", "selectedVersionId": version["id"]}]
        self.assertEqual(workflow.quote(self.store, self.project, {"node_id": "views", "graph": graph})["input_snapshot"]["inputs"][1]["version_id"], version["id"])
        graph["edges"] = graph["edges"][:1]
        self.assertEqual(len(workflow.quote(self.store, self.project, {"node_id": "views", "graph": graph})["input_snapshot"]["inputs"]), 1)

    def test_graph_rejects_wrong_port_and_cycle(self):
        graph = self.graph()
        graph["edges"][0]["targetPort"] = "prompt"
        with self.assertRaisesRegex(ValueError, "端口类型不兼容"):
            workflow.quote(self.store, self.project, {"node_id": "generate", "graph": graph})
        graph = self.graph()
        graph["edges"].append({"id": "cycle", "fromNodeId": "generate", "toNodeId": "prompt", "targetPort": "image"})
        with self.assertRaisesRegex(ValueError, "循环"):
            workflow.quote(self.store, self.project, {"node_id": "generate", "graph": graph})

    def test_submit_and_sync_are_idempotent_and_keep_provenance(self):
        version = workflow.confirm(self.store, self.project, {"node_id": "prompt", "kind": "commerce:prompt", "text": "白底展示"})
        body = {"node_id": "generate", "graph": self.graph(version["id"])}
        offer = workflow.quote(self.store, self.project, body)
        run_body = {**body, "approved_fingerprint": offer["fingerprint"], "request_id": "request-456"}
        with patch.object(workflow, "provider_key", return_value="test"), patch.object(workflow.seeany, "upload_image", return_value="https://example.test/input.png"), patch.object(workflow.seeany, "submit", return_value={"data": {"task_uuid": "remote-1"}}) as submit:
            task = workflow.run(self.store, self.project, run_body)
            self.assertEqual(workflow.run(self.store, self.project, run_body)["id"], task["id"])
            submit.assert_called_once()
        self.assertEqual(task["input_snapshot"]["inputs"][1]["version_id"], version["id"])
        with patch.object(service.seeany, "task_status", return_value={"data": {"status": "succeeded"}}), patch.object(service.seeany, "result_assets", return_value=["https://example.test/result.png"]), patch.object(service.seeany, "download_image", return_value=(png(), ".png")) as download:
            service.sync(self.store, self.project, task["id"])
            service.sync(self.store, self.project, task["id"])
            download.assert_called_once()
        self.assertEqual(len(task["asset_ids"]), 1)
        source = next(item for item in self.project["sources"] if item["id"] == task["asset_ids"][0])
        self.assertEqual(source["task_id"], task["id"])
        self.assertEqual(source["reference_ids"], [self.source["id"]])
        self.assertEqual(len(self.store.load(self.project["id"])["workflow_versions"]), 1)

    def test_restore_keeps_versions_tasks_and_source_without_credentials(self):
        version = workflow.confirm(self.store, self.project, {"node_id": "prompt", "kind": "commerce:prompt", "text": "白底展示"})
        self.project["tasks"].append({"id": "saved-task", "provider": "SeeAny", "kind": "workflow_image",
            "node_id": "generate", "status": "待核对", "remote_id": "remote-42", "input_snapshot": {"inputs": [{"source_id": self.source["id"], "version_id": version["id"]}]},
            "estimate": None, "actual": None, "currency": None})
        self.store.save(self.project)
        restored = backup.restore_project(self.store, backup.export_project(self.store, self.project))
        self.assertNotEqual(restored["id"], self.project["id"])
        self.assertEqual(restored["workflow_versions"][0]["id"], version["id"])
        self.assertEqual(restored["tasks"][0]["remote_id"], "remote-42")
        self.assertEqual(self.store.source_bytes(restored, self.source["id"])[1], png())

    def test_empty_remote_result_stays_checkable(self):
        task = {"id": "empty-task", "provider": "SeeAny", "kind": "workflow_image", "node_id": "generate",
                "status": "远端运行中", "remote_id": "remote-empty", "asset_ids": [], "input_snapshot": {"inputs": []},
                "estimate": None, "actual": None, "currency": None}
        self.project["tasks"].append(task)
        self.store.save(self.project)
        with patch.object(service.seeany, "task_status", return_value={"data": {"status": "succeeded"}}), patch.object(service.seeany, "result_assets", return_value=[]):
            with self.assertRaisesRegex(ValueError, "未返回可导入图片"):
                service.sync(self.store, self.project, task["id"])
        self.assertEqual(task["status"], "待核对")
        self.assertEqual(task["remote_id"], "remote-empty")

    def test_unknown_submission_blocks_new_charge_until_manual_resolution(self):
        version = workflow.confirm(self.store, self.project, {"node_id": "prompt", "kind": "commerce:prompt", "text": "白底展示"})
        body = {"node_id": "generate", "graph": self.graph(version["id"])}
        offer = workflow.quote(self.store, self.project, body)
        first = {**body, "approved_fingerprint": offer["fingerprint"], "request_id": "request-unknown"}
        with patch.object(workflow, "provider_key", return_value="test"), patch.object(workflow.seeany, "upload_image", return_value="https://example.test/input.png"), patch.object(workflow.seeany, "submit", side_effect=TimeoutError("超时")) as submit:
            with self.assertRaisesRegex(ValueError, "勿重复提交"):
                workflow.run(self.store, self.project, first)
            self.assertEqual(workflow.run(self.store, self.project, first)["status"], "待核对")
            with self.assertRaisesRegex(ValueError, "待核对任务"):
                workflow.run(self.store, self.project, {**first, "request_id": "request-new"})
            submit.assert_called_once()
        task = self.project["tasks"][-1]
        self.assertIsNone(task["remote_id"])
        self.store.resolve_unidentified_task(self.project, task["id"], "供应商后台按时间查询无此任务")
        self.assertEqual(task["status"], "失败")


if __name__ == "__main__":
    unittest.main()
