import { useState } from "react";
import { App, Button, Modal } from "antd";
import localforage from "localforage";
import { useTranslation } from "react-i18next";

import { useAssetStore } from "@/stores/use-asset-store";
import { useCanvasStore } from "@/stores/canvas/use-canvas-store";
import { useCanvasUiStore } from "@/stores/canvas/use-canvas-ui-store";
import { studioApi } from "@/services/api/commerce-studio";

export function CanvasDeleteProjectsDialog() {
    const { t } = useTranslation();
    const { message } = App.useApp();
    const [deleting, setDeleting] = useState(false);
    const ids = useCanvasUiStore((state) => state.deleteProjectIds);
    const setDeleteIds = useCanvasUiStore((state) => state.setDeleteProjectIds);
    const removeSelectedIds = useCanvasUiStore((state) => state.removeSelectedProjectIds);
    const deleteProjects = useCanvasStore((state) => state.deleteProjects);
    const cleanupImages = useAssetStore((state) => state.cleanupImages);
    const confirm = async () => {
        setDeleting(true);
        const deleted: string[] = [];
        try {
            const projects = useCanvasStore.getState().projects;
            const links = new Map(await Promise.all(projects.map(async (project) => [project.id, await localforage.getItem<string>(`commerce-studio:${project.id}`)] as const)));
            for (const id of ids) {
                const commerceId = links.get(id);
                if (commerceId && !projects.some((project) => project.id !== id && !deleted.includes(project.id) && links.get(project.id) === commerceId)) {
                    await studioApi.delete(commerceId);
                }
                deleteProjects([id]);
                deleted.push(id);
                await localforage.removeItem(`commerce-studio:${id}`);
            }
            removeSelectedIds(deleted);
            setDeleteIds([]);
        } catch (cause) {
            message.error(cause instanceof Error ? cause.message : "删除商品项目失败");
            removeSelectedIds(deleted);
            setDeleteIds(ids.filter((id) => !deleted.includes(id)));
        } finally {
            if (deleted.length) cleanupImages();
            setDeleting(false);
        }
    };

    return (
        <Modal
            title={t("canvas.project.deleteTitle")}
            open={ids.length > 0}
            centered
            onCancel={() => { if (!deleting) setDeleteIds([]); }}
            footer={
                <>
                    <Button disabled={deleting} onClick={() => setDeleteIds([])}>{t("common.cancel")}</Button>
                    <Button danger type="primary" loading={deleting} onClick={() => void confirm()}>
                        {t("common.delete")}
                    </Button>
                </>
            }
        >
            <p className="text-sm text-stone-500">{t("canvas.project.deleteDescription", { count: ids.length })}</p>
            <p className="text-sm text-stone-500">关联的本机商品资料、生成图和视频也会永久删除；远端 Flova 项目不会被删除。建议先导出完整备份。</p>
        </Modal>
    );
}
