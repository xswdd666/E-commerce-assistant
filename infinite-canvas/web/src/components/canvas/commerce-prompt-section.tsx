import { useEffect, useState } from "react";
import { Button, Input, Select, Space, Tag, Typography } from "antd";

import { studioApi, type PromptFields, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";

type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void>; onInsertImage: (dataUrl: string, title: string, commerceImage?: { projectId: string; sourceId: string; masterVersionId: string }, commercePromptImage?: { projectId: string; sourceId: string; versionId: string }) => Promise<void>; onInsertText: (text: string, title: string, source?: { projectId: string; kind: "prompt"; versionId: string; parentVersionId: string | null }) => void };
const FIELD_LABELS: Record<Exclude<keyof PromptFields, "ratio">, string> = {
    product: "产品特征", scene: "场景", composition: "构图", lighting: "光线", style: "风格", negative: "禁止变化项", purpose: "目标用途",
};
const EMPTY: PromptFields = { product: "", scene: "", composition: "", lighting: "", style: "真实商品广告摄影", negative: "", purpose: "商品详情页", ratio: "1:1" };
const RATIOS = ["1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"];

export function CommercePromptSection({ project, busy, act, onInsertImage, onInsertText }: Props) {
    const [selectedId, setSelectedId] = useState<string>();
    const [fields, setFields] = useState<PromptFields>(EMPTY);
    const [changedFields, setChangedFields] = useState<string[]>(["scene"]);
    const [instruction, setInstruction] = useState("");
    const [changeOffer, setChangeOffer] = useState<StudioQuote | null>(null);
    const [previewOffer, setPreviewOffer] = useState<StudioQuote | null>(null);
    const [referenceId, setReferenceId] = useState<string>();
    const [comparison, setComparison] = useState<{ original: string; generated: string }>();
    const [externalPrompt, setExternalPrompt] = useState("");
    const master = project.master_versions.at(-1);
    const selected = project.prompt_versions.find((version) => version.id === selectedId);
    const dirty = Boolean(selected && JSON.stringify(selected.fields) !== JSON.stringify(fields));
    const current = selected && selected.brief_id === project.brief_versions.at(-1)?.id && selected.master_id === master?.id;
    const candidates = project.sources.filter((source) => source.prompt_version_id === selectedId);

    useEffect(() => {
        const latest = project.prompt_versions.at(-1);
        if (latest) { setSelectedId(latest.id); setFields({ ...latest.fields }); }
        else if (project.brief_versions.length) {
            setFields((value) => ({ ...value, product: value.product || project.brief_versions.at(-1)!.facts.map((fact) => `${fact.field}：${fact.value}`).join("；") }));
        }
    }, [project.prompt_versions.at(-1)?.id]);
    useEffect(() => { setReferenceId(master?.asset_ids[0]); setPreviewOffer(null); }, [master?.id]);

    const choose = (id: string) => {
        const version = project.prompt_versions.find((item) => item.id === id);
        setSelectedId(id);
        setFields(version ? { ...version.fields } : EMPTY);
        setChangeOffer(null);
        setPreviewOffer(null);
        setComparison(undefined);
    };
    const update = (key: keyof PromptFields, value: string) => { setFields((currentFields) => ({ ...currentFields, [key]: value })); setChangeOffer(null); setPreviewOffer(null); };
    const showComparison = async (sourceId: string) => {
        const candidate = project.sources.find((source) => source.id === sourceId);
        if (!candidate?.reference_ids?.[0]) throw new Error("候选缺少参考母版");
        const [original, generated] = await Promise.all([
            studioApi.sourceData(project.id, candidate.reference_ids[0]), studioApi.sourceData(project.id, sourceId),
        ]);
        setComparison({ original: original.data_url, generated: generated.data_url });
    };

    return <section>
        <Typography.Title level={5}>提示词方向与单张试图</Typography.Title>
        <Typography.Paragraph type="secondary">产品、场景、构图、光线、风格、画幅、禁止变化项及目标用途分别编辑。修改产生新版本；DeepSeek 每轮给出 2 至 3 个方向，用户逐个查看费用后再单张试图。</Typography.Paragraph>
        {project.prompt_versions.length ? <Select className="w-full" value={selectedId} onChange={choose} options={project.prompt_versions.map((version, index) => ({ value: version.id, label: `${index + 1}. ${version.origin} · ${version.fields.scene.slice(0, 18)}` }))} /> : null}
        {selected && !current ? <Typography.Paragraph type="warning">此提示词依据旧简报或母版；请保存基于当前输入的新版本。</Typography.Paragraph> : null}
        <Space direction="vertical" className="mt-2 w-full">
            {(Object.keys(FIELD_LABELS) as Array<keyof typeof FIELD_LABELS>).map((key) => <div key={key}><Typography.Text>{FIELD_LABELS[key]}</Typography.Text><Input.TextArea rows={key === "product" ? 3 : 2} value={fields[key]} onChange={(event) => update(key, event.target.value)} /></div>)}
            <Select value={fields.ratio} onChange={(ratio) => update("ratio", ratio)} options={RATIOS.map((ratio) => ({ value: ratio, label: ratio }))} />
            <Button disabled={busy || !master || Object.values(fields).some((value) => !value.trim())} onClick={() => void act(() => studioApi.promptSave(project.id, fields, selectedId || null))}>保存提示词新版本</Button>
        </Space>
        {selected ? <div className="mt-3">
            <Typography.Text strong>完整提示词</Typography.Text><Typography.Paragraph className="mt-1 whitespace-pre-wrap">{selected.text}</Typography.Paragraph><Button disabled={!current} onClick={() => onInsertText(selected.text, `广告提示词 ${selected.id.slice(0, 8)}`, { projectId: project.id, kind: "prompt", versionId: selected.id, parentVersionId: selected.parent_id })}>将此版本加入画布文本节点</Button>
            <Typography.Text type="secondary">相对父版本变化：{selected.changed_fields.map((field) => FIELD_LABELS[field as keyof typeof FIELD_LABELS] || field).join("、") || "无"}</Typography.Text>
            {dirty ? <Typography.Paragraph type="warning">当前编辑尚未保存；AI 改写和试图将读取已保存版本。</Typography.Paragraph> : null}
            <div className="mt-3"><Typography.Text strong>只修改选定字段</Typography.Text>
                <Select mode="multiple" className="mt-2 w-full" value={changedFields} onChange={(value) => { setChangedFields(value); setChangeOffer(null); }} options={Object.entries(FIELD_LABELS).map(([value, label]) => ({ value, label }))} />
                <Input.TextArea className="mt-2" rows={2} placeholder="例如：只换成卧室场景，保持产品角度" value={instruction} onChange={(event) => { setInstruction(event.target.value); setChangeOffer(null); }} />
                <Button disabled={busy || !current || dirty || !changedFields.length || !instruction.trim()} onClick={() => void act(async () => { setChangeOffer(await studioApi.promptChangeQuote(project.id, selected.id, changedFields, instruction)); })}>查看 DeepSeek 输入与费用</Button>
                {changeOffer ? <div className="mt-2"><Typography.Text>只修改：{changedFields.map((field) => FIELD_LABELS[field as keyof typeof FIELD_LABELS]).join("、")}；费用：{changeOffer.estimate == null ? "未知" : changeOffer.estimate}</Typography.Text><Button type="primary" className="ml-2" disabled={busy} onClick={() => void act(async () => { await studioApi.promptChangeRun(project.id, selected.id, changedFields, instruction, changeOffer.fingerprint, crypto.randomUUID()); setChangeOffer(null); })}>确认生成 2–3 个方向</Button></div> : null}
            </div>
            <div className="mt-3"><Typography.Text strong>单张预览</Typography.Text>
                <Select className="mt-2 w-full" value={referenceId} onChange={(id) => { setReferenceId(id); setPreviewOffer(null); }} options={(master?.asset_ids || []).map((id) => ({ value: id, label: project.sources.find((source) => source.id === id)?.view_label || id }))} />
                <Button className="mt-2" disabled={busy || !current || dirty || !referenceId} onClick={() => void act(async () => { setPreviewOffer(await studioApi.promptPreviewQuote(project.id, selected.id, referenceId!)); })}>查看 SeeAny 单张试图输入</Button>
                {previewOffer && referenceId ? <div className="mt-2"><Typography.Paragraph>提示词版本：{selected.id.slice(0, 12)}；母版参考图：{project.sources.find((source) => source.id === referenceId)?.view_label}；费用：{previewOffer.estimate == null ? "未知" : previewOffer.estimate}</Typography.Paragraph><Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.promptPreviewRun(project.id, selected.id, referenceId, previewOffer.fingerprint, crypto.randomUUID()); setPreviewOffer(null); })}>确认提交单张试图</Button></div> : null}
                <div className="mt-3 border-t pt-2"><Typography.Text strong>导入已有 GPT 图片</Typography.Text><Typography.Paragraph type="secondary">保存实际使用的 GPT 提示词与上方选择的母版参考图；导入后仍需对照原图审核真实性。</Typography.Paragraph><Input.TextArea rows={2} placeholder="生成该图片时实际使用的 GPT 提示词" value={externalPrompt} onChange={(event) => setExternalPrompt(event.target.value)} /><input className="mt-2" type="file" accept="image/jpeg,image/png,image/webp" disabled={busy || !current || dirty || !referenceId || !externalPrompt.trim()} onChange={(event) => { const file = event.target.files?.[0]; if (file && referenceId) void act(async () => { await studioApi.promptImport(project.id, file, selected.id, referenceId, externalPrompt); setExternalPrompt(""); }); event.target.value = ""; }} /></div>
                {project.tasks.filter((task) => task.kind === "prompt_preview" && task.input_snapshot?.version_id === selected.id).map((task) => <div key={task.id} className="mt-1"><Tag>{task.status}</Tag>试图任务<Button type="link" disabled={busy} onClick={() => void act(() => studioApi.sync(project.id, task.id))}>同步结果</Button></div>)}
                {candidates.map((source) => <div key={source.id} className="mt-1"><Tag color={project.prompt_adoption?.source_id === source.id ? "success" : "default"}>{project.prompt_adoption?.source_id === source.id ? "当前采用" : "候选"}</Tag>{source.origin === "GPT import" ? <Tag>GPT 导入</Tag> : null}{source.name}<Space><Button type="link" disabled={busy} onClick={() => void act(() => showComparison(source.id))}>对照原图</Button><Button type="link" disabled={busy} onClick={() => void act(() => studioApi.promptReview(project.id, selected.id, source.id, "采用"))}>真实性通过并采用</Button><Button type="link" disabled={busy} onClick={() => { const reason = window.prompt("废图原因"); if (reason?.trim()) void act(() => studioApi.promptReview(project.id, selected.id, source.id, "废图", reason)); }}>废图</Button><Button type="link" disabled={busy} onClick={() => void act(async () => { const asset = await studioApi.sourceData(project.id, source.id); await onInsertImage(asset.data_url, source.name, undefined, { projectId: project.id, sourceId: source.id, versionId: selected.id }); })}>加入画布分支</Button></Space>{source.import_prompt ? <Typography.Paragraph className="mt-1 whitespace-pre-wrap" type="secondary">实际生成提示词：{source.import_prompt}</Typography.Paragraph> : null}</div>)}
                {comparison ? <div className="mt-2 grid grid-cols-2 gap-2"><div><Typography.Text>母版参考</Typography.Text><img src={comparison.original} alt="母版参考" className="w-full" /></div><div><Typography.Text>生成候选</Typography.Text><img src={comparison.generated} alt="生成候选" className="w-full" /></div></div> : null}
                {project.prompt_adoption?.version_id === selected.id ? <Button className="mt-2" disabled={busy} onClick={() => void act(async () => { const asset = await studioApi.sourceData(project.id, project.prompt_adoption!.source_id); await onInsertImage(asset.data_url, "当前采用试图", undefined, { projectId: project.id, sourceId: project.prompt_adoption!.source_id, versionId: selected.id }); })}>将采用图加入画布</Button> : null}
            </div>
        </div> : null}
    </section>;
}
