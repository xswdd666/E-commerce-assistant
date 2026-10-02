import { useState } from "react";
import { Button, Select, Typography } from "antd";

import { studioApi, type StudioProject } from "@/services/api/commerce-studio";

type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void> };

export function CommerceDeliverySection({ project, busy, act }: Props) {
    const videos = project.deliverables.filter((item) => item.kind === "video" || item.kind === "finished_video");
    const [videoId, setVideoId] = useState<string>();
    const selectedId = videoId || videos.at(-1)?.id;
    const plan = project.gallery_versions.find((item) => item.id === project.gallery_approval);
    const galleryReady = Boolean(plan?.items.every((item) => project.gallery_choices[item.id]));
    const ready = Boolean(selectedId && galleryReady && project.script_approval && project.storyboard_approval);
    const download = async () => {
        if (!selectedId) return;
        const blob = await studioApi.deliveryBlob(project.id, selectedId);
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `${project.name.replace(/[\\/:*?"<>|]/g, "_")}-交付包.zip`;
        anchor.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    return <section>
        <Typography.Title level={5}>交付包</Typography.Title>
        <Typography.Paragraph type="secondary">包含视频 MP4、逐张套图 PNG 与可编辑文字 SVG、已确认脚本和分镜、采用提示词及素材来源清单。</Typography.Paragraph>
        <Select className="w-full" placeholder="选择交付视频" value={selectedId} onChange={setVideoId} options={videos.map((item) => ({ value: item.id, label: item.kind === "finished_video" ? `本地收尾成片 ${item.id.slice(0, 8)}` : `Flova 原片 ${item.id.slice(0, 8)}` }))} />
        <Typography.Paragraph className="mt-2">套图：{plan ? `${plan.items.filter((item) => project.gallery_choices[item.id]).length}/${plan.items.length} 张已采用` : "未批准"}；脚本与分镜：{project.script_approval && project.storyboard_approval ? "已批准" : "待批准"}</Typography.Paragraph>
        <Button type="primary" disabled={busy || !ready} onClick={() => void act(download)}>下载完整交付包</Button>
        <Typography.Paragraph type="secondary" className="mt-2">拼多多实际上传尺寸、比例和文件大小仍以试点商家后台为准，交付前需人工核对。</Typography.Paragraph>
    </section>;
}
