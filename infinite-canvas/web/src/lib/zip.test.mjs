import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import test from "node:test";
import { zipSync } from "fflate";

import { createZip, readZip } from "./zip.ts";

test("streaming ZIP preserves large nested media and text", async () => {
    const media = new Blob([randomBytes(3 * 1024 * 1024)]);
    const inner = await createZip([{ name: "media.bin", data: media }]);
    const outer = await createZip([{ name: "canvas.zip", data: inner }, { name: "bundle.json", data: "{\"version\":1}" }]);
    const entries = await readZip(outer);
    assert.equal(await entries.get("bundle.json")?.text(), '{"version":1}');
    assert.equal(entries.get("canvas.zip")?.size, inner.size);
    const restored = await readZip(entries.get("canvas.zip"));
    const recovered = restored.get("media.bin");
    assert.ok(recovered, `missing media.bin; found ${[...restored.keys()].join(", ")}`);
    assert.equal(recovered.size, media.size);
    assert.deepEqual(new Uint8Array(await recovered.arrayBuffer()), new Uint8Array(await media.arrayBuffer()));
});

test("streaming reader accepts deflated archives", async () => {
    const compressed = zipSync({ "project.json": new TextEncoder().encode('{"name":"商品"}') });
    const entries = await readZip(new Blob([compressed]));
    assert.equal(await entries.get("project.json")?.text(), '{"name":"商品"}');
});

test("streaming reader rejects a truncated archive", async () => {
    const archive = await createZip([{ name: "project.json", data: "{}" }]);
    await assert.rejects(readZip(archive.slice(0, archive.size - 30)));
});
