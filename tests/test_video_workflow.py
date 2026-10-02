import base64
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from commerce_studio.core import Store
from commerce_studio import video_workflow


class VideoWorkflowTests(unittest.TestCase):
    def test_script_confirm_and_shot_quote(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            project = store.create("video")
            stream = io.BytesIO()
            Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
            source = store.add_source(project, "product.png", "image/png", base64.b64encode(stream.getvalue()).decode())
            script = video_workflow.confirm(store, project, {"node_id": "script", "kind": video_workflow.SCRIPT_KIND,
                "shots": [{"index": i + 1, "duration": 4, "visual": "商品"} for i in range(4)]})
            graph = {"nodes": [{"id": "upload", "kind": "commerce:upload", "source_id": source["id"]},
                                {"id": "script", "kind": video_workflow.SCRIPT_KIND},
                                {"id": "shot", "kind": video_workflow.SHOT_KIND, "shot_index": 0}],
                     "edges": [{"id": "image", "fromNodeId": "upload", "toNodeId": "shot", "targetPort": "image"},
                               {"id": "script", "fromNodeId": "script", "toNodeId": "shot", "targetPort": "script", "selectedVersionId": script["id"]}]}
            offer = video_workflow.quote(store, project, {"node_id": "shot", "graph": graph})
            self.assertEqual(offer["provider"], "Flova")
            self.assertEqual(offer["input_snapshot"]["duration"], 4)


if __name__ == "__main__":
    unittest.main()
