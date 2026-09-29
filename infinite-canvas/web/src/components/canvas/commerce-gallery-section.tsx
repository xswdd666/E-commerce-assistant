import { useEffect, useState } from "react";
import { Button, Input, InputNumber, Select, Space, Tag, Typography } from "antd";

import { studioApi, type GalleryItem, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";

type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void> };
type ImageOffer = { itemId: string; quote: StudioQuote };
const RATIOS = ["1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"];

export function CommerceGallerySection({ project, busy, act }: Props) {
    const latest = project.gallery_versions.at(-1);
    const [draft, setDraft] = useState<GalleryItem[]>([]);
    const [requirement, setRequirement] = useState("");
    const [planOffer, setPlanOffer] = useState<StudioQuote | null>(null);
    const [imageOffer, setImageOffer] = useState<ImageOffer | null>(null);
    const [previewUrls, setPreviewUrls] = useState<Record<string, string>>({});
    const dirty = latest ? JSON.stringify(draft) !== JSON.stringify(latest.items) : false;

    useEffect(() => { setDraft(latest ? structuredClone(latest.items) : []); setImageOffer(null); }, [latest?.id]);

    const update = (index: number, patch: Partial<GalleryItem>) => setDraft((current) => current.map((item, i) => i === index ? { ...item, ...patch } : item));
    const move = (index: number, shift: number) => setDraft((current) => {
        const next = [...current];
        const other = index + shift;
        if (other < 0 || other >= next.length) return current;
        [next[index], next[other]] = [next[other], next[index]];
        return next;
    });
    const showImage = async (sourceId: string) => {
        if (previewUrls[sourceId]) return;
        try {
            const file = await studioApi.sourceData(project.id, sourceId);
            setPreviewUrls((current) => ({ ...current, [sourceId]: file.data_url }));
        } catch (cause) {
            window.alert(cause instanceof Error ? cause.message : "无法读取图片");
        }
    };
    const download = async () => {
        const blob = await studioApi.galleryExport(project.id);
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `${project.name}-套图.zip`;
        anchor.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    };

    return <section>
        <Typography.Title level={5}>拼多多电商套图</Typography.Title>
        <Typography.Paragraph type="secondary">视频与套图共用已确认三视图母版。默认六张；每次编辑保存为新方案版本，文字层与底图分别保存。</Typography.Paragraph>
        <Space wrap>
            <Button disabled={busy || !project.master_versions.length} onClick={() => void act(() => studioApi.galleryDefault(project.id))}>新建默认六图方案</Button>
            <Button disabled={busy || !project.master_versions.length} onClick={() => void act(async () => { setPlanOffer(await studioApi.galleryPlanQuote(project.id, requirement)); })}>查看 SeeAny 策划输入</Button>
        </Space>
        <Input.TextArea className="mt-2" rows={2} placeholder="套图策划补充要求" value={requirement} onChange={(event) => { setRequirement(event.target.value); setPlanOffer(null); }} />
        {planOffer ? <div className="mt-2"><Typography.Text>已确认简报与母版、三张参考图；预计费用：{planOffer.estimate == null ? "未知" : `${planOffer.estimate.toFixed(2)} ${planOffer.currency || "单位未知"}`}；{planOffer.pricing_source}</Typography.Text><Button type="primary" className="ml-2" disabled={busy} onClick={() => void act(async () => { await studioApi.galleryPlanRun(project.id, requirement, planOffer.fingerprint, crypto.randomUUID()); setPlanOffer(null); })}>确认提交套图策划</Button></div> : null}
        {project.tasks.filter((task) => task.kind === "gallery_plan").map((task) => <div key={task.id} className="mt-1"><Tag>{task.status}</Tag>SeeAny 套图策划</div>)}
        {latest ? <div className="mt-3">
            <Typography.Paragraph>最新方案：{latest.origin} · {latest.items.length} 张 {project.gallery_approval === latest.id ? <Tag color="success">已批准</Tag> : <Tag>待批准</Tag>}</Typography.Paragraph>
            {dirty ? <Typography.Paragraph type="warning">方案草稿已修改；先保存并批准新版本，再生成图片。</Typography.Paragraph> : null}
            {draft.map((item, index) => {
                const candidates = project.sources.filter((source) => source.gallery_item_id === item.id && source.gallery_plan_id === latest.id);
                const chosen = project.gallery_choices[item.id];
                const tasks = project.tasks.filter((task) => task.kind === "gallery_image" && task.input_snapshot?.item?.id === item.id);
                return <div key={item.id} className="mt-3 border-t pt-2">
                    <Space className="mb-2"><Typography.Text strong>{index + 1}. {item.kind}</Typography.Text><Button size="small" disabled={index === 0} onClick={() => move(index, -1)}>上移</Button><Button size="small" disabled={index === draft.length - 1} onClick={() => move(index, 1)}>下移</Button><Button size="small" disabled={draft.length === 1} onClick={() => setDraft((current) => current.filter((_, i) => i !== index))}>删除</Button></Space>
                    <Input className="mb-2" value={item.kind} onChange={(event) => update(index, { kind: event.target.value })} placeholder="图片类型" />
                    <Input.TextArea rows={3} value={item.prompt} onChange={(event) => update(index, { prompt: event.target.value })} placeholder="单张提示词" />
                    <Select className="mt-2 w-full" value={item.ratio} onChange={(ratio) => update(index, { ratio })} options={RATIOS.map((ratio) => ({ value: ratio, label: ratio }))} />
                    <Space direction="vertical" className="mt-2 w-full">
                        <Input placeholder="可编辑标题" value={item.title} onChange={(event) => update(index, { title: event.target.value })} />
                        <Input placeholder="可编辑副标题" value={item.subtitle} onChange={(event) => update(index, { subtitle: event.target.value })} />
                        <Space><Typography.Text type="secondary">文字位置 %</Typography.Text><InputNumber min={0} max={100} value={item.x} onChange={(x) => update(index, { x: x ?? 50 })} /><InputNumber min={0} max={100} value={item.y} onChange={(y) => update(index, { y: y ?? 10 })} /></Space>
                    </Space>
                    {project.gallery_approval === latest.id && !dirty ? <div className="mt-2">
                        <Button disabled={busy} onClick={() => void act(async () => { setImageOffer({ itemId: item.id, quote: await studioApi.galleryImageQuote(project.id, item.id) }); })}>查看单张出图输入</Button>
                        {imageOffer?.itemId === item.id ? <div className="mt-2"><Typography.Paragraph className="whitespace-pre-wrap">提示词：{item.prompt}<br />费用：{imageOffer.quote.estimate == null ? "未知" : imageOffer.quote.estimate}</Typography.Paragraph><Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.galleryImageRun(project.id, item.id, imageOffer.quote.fingerprint, crypto.randomUUID()); setImageOffer(null); })}>确认提交这张套图</Button></div> : null}
                    </div> : null}
                    {tasks.map((task) => <div key={task.id}><Tag>{task.status}</Tag><Button type="link" disabled={busy} onClick={() => void act(() => studioApi.sync(project.id, task.id))}>同步结果</Button></div>)}
                    {candidates.map((source) => <div key={source.id} className="mt-1">
                        <Tag color={chosen === source.id ? "success" : "default"}>{chosen === source.id ? "当前采用" : "候选"}</Tag>{source.name}
                        <Button type="link" onClick={() => void showImage(source.id)}>查看</Button>
                        <Button type="link" disabled={busy} onClick={() => void act(() => studioApi.galleryReview(project.id, item.id, source.id, "采用"))}>真实性通过并采用</Button>
                        <Button type="link" disabled={busy} onClick={() => { const reason = window.prompt("废图原因：结构错误、场景不真实、卖点不清楚等"); if (reason?.trim()) void act(() => studioApi.galleryReview(project.id, item.id, source.id, "废图", reason)); }}>废图</Button>
                        {previewUrls[source.id] ? <img src={previewUrls[source.id]} alt={item.kind} className="mt-1 w-full" /> : null}
                    </div>)}
                </div>;
            })}
            <Space wrap className="mt-3"><Button disabled={draft.length >= 12} onClick={() => setDraft((current) => [...current, { id: crypto.randomUUID(), kind: "新图片", prompt: "依据已确认事实与三视图母版生成电商图片，不生成可读文字", ratio: "1:1", title: "", subtitle: "", x: 50, y: 10 }])}>增加一张</Button><Button disabled={busy || draft.some((item) => !item.kind.trim() || !item.prompt.trim())} onClick={() => void act(() => studioApi.gallerySave(project.id, draft, latest.id))}>保存方案新版本</Button><Button type="primary" disabled={busy || project.gallery_approval === latest.id} onClick={() => void act(() => studioApi.galleryApprove(project.id, latest.id))}>批准最新方案</Button></Space>
            {project.gallery_approval === latest.id ? <div className="mt-3"><Typography.Text>已采用 {latest.items.filter((item) => project.gallery_choices[item.id]).length} / {latest.items.length} 张</Typography.Text><Button className="ml-2" disabled={busy || latest.items.some((item) => !project.gallery_choices[item.id])} onClick={() => void act(download)}>导出 PNG、可编辑 SVG 与来源清单</Button></div> : null}
        </div> : null}
    </section>;
}
