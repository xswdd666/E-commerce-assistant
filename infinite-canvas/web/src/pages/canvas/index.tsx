import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { App, Button, Input } from "antd";
import { Download, FileUp, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import localforage from "localforage";

import { readZip } from "@/lib/zip";
import { setMediaBlob } from "@/services/file-storage";
import { setImageBlob } from "@/services/image-storage";
import { CanvasDeleteProjectsDialog } from "@/components/canvas/canvas-delete-projects-dialog";
import { CanvasProjectCard } from "@/components/canvas/canvas-project-card";
import type { CanvasExportFile } from "@/types/canvas-export";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { useCanvasUiStore } from "@/stores/canvas/use-canvas-ui-store";
import { exportCanvasProjects } from "@/lib/canvas/canvas-export";
import { hasAgentUrlBootstrap } from "@/lib/agent/agent-url-bootstrap";
import { studioApi } from "@/services/api/commerce-studio";

export default function CanvasPage() {
    const { message } = App.useApp();
    const { t } = useTranslation();
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const [query, setQuery] = useState("");
    const [searching, setSearching] = useState(false);
    const [results, setResults] = useState<Array<{ project_id: string; project_name: string; kind: string; text: string; canvasId?: string }>>([]);
    const inputRef = useRef<HTMLInputElement>(null);
    const autoOpenRef = useRef(false);
    const hydrated = useCanvasStore((state) => state.hydrated);
    const projects = useCanvasStore((state) => state.projects);
    const createProject = useCanvasStore((state) => state.createProject);
    const importProject = useCanvasStore((state) => state.importProject);
    const selectedIds = useCanvasUiStore((state) => state.selectedProjectIds);
    const setDeleteIds = useCanvasUiStore((state) => state.setDeleteProjectIds);

    const mode = searchParams.get("mode");
    const agentMode = mode === "new" || mode === "recent" || mode === "choose";
    const agentQuery = agentMode ? `?${searchParams.toString()}` : "";
    const enterProject = (id: string) => {
        const agentHash = hasAgentUrlBootstrap(window.location.hash) ? window.location.hash : "";
        navigate(`/canvas/${id}${agentQuery}${agentHash}`, { replace: Boolean(agentHash) });
    };
    const createAndEnter = () => enterProject(createProject(t("canvas.defaultTitle", { count: projects.length + 1 })));
    const searchCommerce = async () => {
        if (!query.trim()) { setResults([]); return; }
        setSearching(true);
        try {
            const [matches, links] = await Promise.all([studioApi.search(query), Promise.all(projects.map(async (item) => ({ canvasId: item.id, commerceId: await localforage.getItem<string>(`commerce-studio:${item.id}`) })))]);
            setResults(matches.map((item) => ({ ...item, canvasId: links.find((link) => link.commerceId === item.project_id)?.canvasId })));
        } catch (cause) { message.error(cause instanceof Error ? cause.message : "搜索失败"); }
        finally { setSearching(false); }
    };
    const importCanvas = async (file?: File) => {
        if (!file) return;
        try {
            const zip = await readZip(file);
            const bundleFile = zip.get("bundle.json");
            if (bundleFile) {
                const bundle = JSON.parse(await bundleFile.text()) as { format?: string; version?: number };
                if (bundle.format !== "commerce-studio-bundle" || bundle.version !== 1) throw new Error("unsupported commerce bundle");
                const canvasFile = zip.get("canvas.zip");
                const commerceFile = zip.get("commerce.zip");
                if (!canvasFile || !commerceFile) throw new Error("missing bundle component");
                const canvasZip = await readZip(canvasFile);
                const projectFile = canvasZip.get("projects.json");
                if (!projectFile) throw new Error("missing canvas project");
                const data = JSON.parse(await projectFile.text()) as CanvasExportFile;
                if (data.app !== "infinite-canvas" || data.version !== 3 || data.projects.length !== 1) throw new Error("invalid canvas project");
                const exported = data.projects[0];
                const newKeys = new Map<string, string>();
                for (const item of exported.files) {
                    if (!canvasZip.has(item.path)) throw new Error("missing canvas asset");
                    newKeys.set(item.storageKey, `${item.storageKey.split(":", 1)[0]}:${crypto.randomUUID()}`);
                }
                const restored = await studioApi.restoreBlob(commerceFile);
                await Promise.all(exported.files.map(async (item) => {
                    const blob = canvasZip.get(item.path)!;
                    const typed = blob.slice(0, blob.size, item.mimeType);
                    const key = newKeys.get(item.storageKey)!;
                    await (key.startsWith("image:") ? setImageBlob(key, typed) : setMediaBlob(key, typed));
                }));
                const remapped = JSON.parse(JSON.stringify(exported.project, (_key, value) => typeof value === "string" ? newKeys.get(value) || value : value));
                for (const node of remapped.nodes) {
                    if (node.metadata?.commerceAsset?.projectId === restored.restored_from) node.metadata.commerceAsset.projectId = restored.id;
                    if (node.metadata?.commerceSource?.projectId === restored.restored_from) node.metadata.commerceSource.projectId = restored.id;
                    if (node.metadata?.commerceImage?.projectId === restored.restored_from) node.metadata.commerceImage.projectId = restored.id;
                }
                const newCanvasId = importProject(remapped);
                await localforage.setItem(`commerce-studio:${newCanvasId}`, restored.id);
                message.success("完整商品项目已恢复；远端任务状态请重新核对");
                return;
            }
            const projectFile = zip.get("projects.json");
            if (!projectFile) throw new Error("missing projects.json");
            const data = JSON.parse(await projectFile.text()) as CanvasExportFile;
            await Promise.all(
                data.projects.flatMap((project) =>
                    project.files.map(async (item) => {
                        const blob = zip.get(item.path);
                        if (!blob) return;
                        const typedBlob = blob.type ? blob : blob.slice(0, blob.size, item.mimeType);
                        await (item.storageKey.startsWith("image:") ? setImageBlob(item.storageKey, typedBlob) : setMediaBlob(item.storageKey, typedBlob));
                    }),
                ),
            );
            data.projects.forEach((item) => importProject(item.project));
            message.success(t("canvas.imported", { count: data.projects.length }));
        } catch (cause) {
            message.error(cause instanceof Error ? cause.message : t("canvas.importFailed"));
        } finally {
            if (inputRef.current) inputRef.current.value = "";
        }
    };

    useEffect(() => {
        if (!hydrated || autoOpenRef.current || (mode !== "new" && mode !== "recent")) return;
        autoOpenRef.current = true;
        enterProject(mode === "new" ? createProject(t("canvas.defaultTitle", { count: projects.length + 1 })) : projects[0]?.id || createProject(t("canvas.defaultTitle", { count: projects.length + 1 })));
    }, [createProject, hydrated, mode, projects, t]);

    if (hydrated && (mode === "new" || mode === "recent")) return <main className="flex h-full items-center justify-center bg-background text-sm text-stone-500">{t("canvas.opening")}</main>;

    return (
        <main className="h-full overflow-auto bg-background text-stone-950 dark:text-stone-100">
            <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-6 py-10">
                <header className="flex flex-wrap items-end justify-between gap-4 border-b border-stone-200 pb-6 dark:border-stone-800">
                    <div>
                        <p className="text-xs text-stone-500">{t("canvas.library")}</p>
                        <h1 className="mt-3 text-3xl font-semibold">{t("canvas.title")}</h1>
                    </div>
                    <div className="flex items-center gap-2">
                        {selectedIds.length ? (
                            <>
                                <Button disabled={!hydrated} icon={<Download className="size-4" />} onClick={() => void exportCanvasProjects(projects.filter((project) => selectedIds.includes(project.id)), `${t("canvas.title")}-${selectedIds.length}`)}>
                                    {t("canvas.exportSelected")}
                                </Button>
                                <Button disabled={!hydrated} onClick={() => setDeleteIds(selectedIds)}>
                                    {t("canvas.deleteSelected")}
                                </Button>
                            </>
                        ) : null}
                        {projects.length ? (
                            <Button disabled={!hydrated} onClick={() => setDeleteIds(projects.map((project) => project.id))}>
                                {t("canvas.deleteAll")}
                            </Button>
                        ) : null}
                        <Button disabled={!hydrated} icon={<FileUp className="size-4" />} onClick={() => inputRef.current?.click()}>
                            {t("canvas.import")}
                        </Button>
                        <Button disabled={!hydrated} type="primary" icon={<Plus className="size-4" />} onClick={createAndEnter}>
                            {t("canvas.create")}
                        </Button>
                    </div>
                </header>

                <section>
                    <div className="flex gap-2"><Input value={query} onChange={(event) => setQuery(event.target.value)} onPressEnter={() => void searchCommerce()} placeholder="搜索商品项目、素材或提示词" /><Button loading={searching} onClick={() => void searchCommerce()}>搜索</Button></div>
                    {results.length ? <div className="mt-3 space-y-2">{results.map((item, index) => <div key={`${item.project_id}-${item.kind}-${index}`} className="flex items-start justify-between gap-3 border-b border-stone-200 py-2 text-sm dark:border-stone-800"><div><span className="font-medium">{item.project_name}</span> · {item.kind}<div className="max-w-3xl break-words text-stone-500">{item.text}</div></div>{item.canvasId ? <Button type="link" onClick={() => enterProject(item.canvasId!)}>打开画布</Button> : <span className="text-stone-500">无关联画布</span>}</div>)}</div> : null}
                </section>

                {!hydrated ? (
                    <section className="flex min-h-[360px] items-center justify-center border-y border-stone-200 text-sm text-stone-500 dark:border-stone-800">{t("canvas.loading")}</section>
                ) : projects.length ? (
                    <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
                        {projects.map((project) => (
                            <CanvasProjectCard key={project.id} project={project} />
                        ))}
                    </div>
                ) : (
                    <section className="flex min-h-[360px] flex-col items-center justify-center border-y border-stone-200 text-center dark:border-stone-800">
                        <h2 className="text-xl font-medium">{t("canvas.empty")}</h2>
                        <p className="mt-3 text-sm text-stone-500">{t("canvas.emptyDescription")}</p>
                        <Button type="primary" className="mt-6" icon={<Plus className="size-4" />} onClick={createAndEnter}>
                            {t("canvas.create")}
                        </Button>
                    </section>
                )}
            </div>

            <input ref={inputRef} type="file" accept="application/zip,.zip" className="hidden" onChange={(event) => void importCanvas(event.target.files?.[0])} />
            <CanvasDeleteProjectsDialog />
        </main>
    );
}
