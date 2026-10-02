import { Unzip, UnzipInflate, Zip, ZipDeflate } from "fflate";

type ZipFile = {
    name: string;
    data: BlobPart;
};

export async function createZip(files: ZipFile[]) {
    const chunks: Blob[] = [];
    let failure: Error | null = null;
    let finished = false;
    const zip = new Zip((error, chunk, final) => {
        if (error) failure = error;
        else if (chunk?.length) chunks.push(new Blob([chunk.slice()]));
        if (final) finished = true;
    });
    for (const file of files) {
        const entry = new ZipDeflate(file.name, { level: 1 });
        zip.add(entry);
        const reader = new Blob([file.data]).stream().getReader();
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            entry.push(value);
            if (failure) throw failure;
        }
        entry.push(new Uint8Array(), true);
        if (failure) throw failure;
    }
    zip.end();
    if (failure) throw failure;
    if (!finished) throw new Error("ZIP 创建未完成");
    return new Blob(chunks, { type: "application/zip" });
}

export async function readZip(file: Blob) {
    const tailStart = Math.max(0, file.size - 65557);
    const tail = new Uint8Array(await file.slice(tailStart).arrayBuffer());
    const view = new DataView(tail.buffer, tail.byteOffset, tail.byteLength);
    let hasEnd = false;
    for (let index = tail.length - 22; index >= 0; index -= 1) {
        if (view.getUint32(index, true) !== 0x06054b50) continue;
        if (index + 22 + view.getUint16(index + 20, true) !== tail.length) continue;
        if (view.getUint32(index + 12, true) + view.getUint32(index + 16, true) !== tailStart + index) continue;
        hasEnd = true;
        break;
    }
    if (!hasEnd) throw new Error("ZIP 文件不完整");
    const entries = new Map<string, Blob>();
    let failure: Error | null = null;
    const unzip = new Unzip((entry) => {
        const chunks: Blob[] = [];
        entry.ondata = (error, chunk, final) => {
            if (error) failure = error;
            else if (chunk?.length) chunks.push(new Blob([chunk.slice()]));
            if (final && !error) entries.set(entry.name, new Blob(chunks));
        };
        entry.start();
    });
    unzip.register(UnzipInflate);
    const reader = file.stream().getReader();
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        unzip.push(value);
        if (failure) throw failure;
    }
    unzip.push(new Uint8Array(), true);
    if (failure) throw failure;
    return entries;
}
