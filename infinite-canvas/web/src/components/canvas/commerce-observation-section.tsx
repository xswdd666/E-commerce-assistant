import { useState } from "react";
import { Button, Input, Select, Space, Tag, Typography } from "antd";

import { studioApi, type StudioProject, type StudioQuote } from "@/services/api/commerce-studio";

type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void> };
const FIELDS = ["颜色", "形状", "结构", "Logo", "文字", "配件", "场景可信度", "卖点表达"];

export function CommerceObservationSection({ project, busy, act }: Props) {
    const [originalId, setOriginalId] = useState<string>();
    const [candidateId, setCandidateId] = useState<string>();
    const [quote, setQuote] = useState<StudioQuote>();
    const [preview, setPreview] = useState<{ original: string; candidate: string }>();
    const [editing, setEditing] = useState("");
    const [correction, setCorrection] = useState("");
    const originals = project.sources.filter((source) => source.mime.startsWith("image/") && !source.origin);
    const candidates = project.sources.filter((source) => source.mime.startsWith("image/") && (source.origin?.startsWith("SeeAny") || source.origin === "GPT import"));

    return <section>
        <Typography.Title level={5}>候选图观察</Typography.Title>
        <Typography.Paragraph type="secondary">DeepSeek 对照实拍逐项描述差异；模型判断仅供参考，不会自动修改产品事实或批准图片。</Typography.Paragraph>
        <Space direction="vertical" className="w-full">
            <Select className="w-full" placeholder="选择原始实拍" value={originalId} onChange={(id) => { setOriginalId(id); setQuote(undefined); setPreview(undefined); }} options={originals.map((source) => ({ value: source.id, label: source.name }))} />
            <Select className="w-full" placeholder="选择 SeeAny 或 GPT 候选图" value={candidateId} onChange={(id) => { setCandidateId(id); setQuote(undefined); setPreview(undefined); }} options={candidates.map((source) => ({ value: source.id, label: source.name }))} />
            <Button disabled={busy || !originalId || !candidateId || !project.brief_versions.length} onClick={() => void act(async () => {
                const [offer, original, candidate] = await Promise.all([studioApi.observationQuote(project.id, originalId!, candidateId!), studioApi.sourceData(project.id, originalId!), studioApi.sourceData(project.id, candidateId!)]);
                setQuote(offer);
                setPreview({ original: original.data_url, candidate: candidate.data_url });
            })}>查看对照输入与费用</Button>
            {preview ? <div className="grid grid-cols-2 gap-2"><div><Typography.Text>原始实拍</Typography.Text><img src={preview.original} alt="原始实拍" className="w-full" /></div><div><Typography.Text>生成候选</Typography.Text><img src={preview.candidate} alt="生成候选" className="w-full" /></div></div> : null}
            {quote ? <><Typography.Text>DeepSeek 图文观察预计费用：{quote.estimate == null ? "未知" : `${quote.estimate} ${quote.currency || "单位未知"}`}；{quote.pricing_source}</Typography.Text><Button type="primary" disabled={busy} onClick={() => void act(async () => { await studioApi.observationRun(project.id, originalId!, candidateId!, quote.fingerprint, crypto.randomUUID()); setQuote(undefined); })}>确认提交图片观察</Button></> : null}
        </Space>
        {(project.image_observations || []).map((item) => <div key={item.id} className="mt-3 border-t pt-2">
            <Typography.Paragraph><Tag>待人工核对</Tag>{project.sources.find((source) => source.id === item.candidate_id)?.name || item.candidate_id}</Typography.Paragraph>
            {FIELDS.map((field) => {
                const corrected = item.corrections.filter((entry) => entry.field === field).at(-1);
                const key = `${item.id}:${field}`;
                return <div key={field} className="mt-2"><Typography.Text strong>{field}：</Typography.Text><Typography.Text>{item.fields[field]}</Typography.Text>
                    {corrected ? <Typography.Paragraph type="secondary" className="mb-1">人工纠正：{corrected.text}</Typography.Paragraph> : null}
                    {editing === key ? <Space direction="vertical" className="w-full"><Input.TextArea rows={2} value={correction} onChange={(event) => setCorrection(event.target.value)} placeholder="写下实际观察或无法判断的原因" /><Space><Button size="small" type="primary" disabled={busy || !correction.trim()} onClick={() => void act(async () => { await studioApi.observationCorrect(project.id, item.id, field, correction); setEditing(""); setCorrection(""); })}>保存纠正</Button><Button size="small" onClick={() => setEditing("")}>取消</Button></Space></Space> : <Button size="small" type="link" onClick={() => { setEditing(key); setCorrection(corrected?.text || ""); }}>纠正</Button>}
                </div>;
            })}
        </div>)}
    </section>;
}
