import { useState } from "react";
import { Button, Input, InputNumber, Select, Space, Tag, Typography } from "antd";

import { studioApi, type StudioCost, type StudioProject } from "@/services/api/commerce-studio";

type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void> };

export function CommerceCostSection({ project, busy, act }: Props) {
    const [editing, setEditing] = useState<string>();
    const [amount, setAmount] = useState<number | null>(null);
    const [currency, setCurrency] = useState("CNY");
    const [receipt, setReceipt] = useState("");
    const [purpose, setPurpose] = useState<string>("全部");
    const groups = new Map<string, { provider: string; stage: string; purpose: string; currency: string; amount: number }>();
    for (const item of project.costs) {
        if (item.actual == null || !item.currency || purpose !== "全部" && item.purpose !== purpose) continue;
        const key = [item.provider, item.stage, item.purpose, item.currency].join("|");
        const group = groups.get(key) || { provider: item.provider, stage: item.stage, purpose: item.purpose, currency: item.currency, amount: 0 };
        group.amount += item.actual;
        groups.set(key, group);
    }
    const startEditing = (item: StudioCost) => {
        setEditing(item.task_id);
        setAmount(item.actual);
        setCurrency(item.currency || "CNY");
        setReceipt(item.receipt_source || "");
    };

    return <section>
        <Typography.Title level={5}>费用流水</Typography.Title>
        <Typography.Paragraph>共 {project.costs.length} 笔任务；{project.costs.filter((item) => item.actual == null).length} 笔待结算。未知预估和实际账单分开显示，不因价格未知阻止手动提交。</Typography.Paragraph>
        <Select value={purpose} onChange={setPurpose} options={["全部", "草稿", "正式"].map((value) => ({ value, label: value }))} />
        {[...groups.values()].map((group) => <div key={[group.provider, group.stage, group.purpose, group.currency].join("|")} className="mt-2"><Tag>{group.provider}</Tag>{group.stage} · {group.purpose} · 已确认 {group.amount.toFixed(2)} {group.currency}</div>)}
        {project.costs.filter((item) => purpose === "全部" || item.purpose === purpose).map((item) => <div key={item.id} className="mt-3 border-t pt-2">
            <Typography.Text><Tag>{item.provider}</Tag>{item.stage} · {item.purpose} · {item.status}</Typography.Text>
            <Typography.Paragraph className="mb-1">预计：{item.estimate == null ? "未知" : `${item.estimate} ${item.currency || "单位未核实"}`}；实付：{item.actual == null ? "待结算" : `${item.actual} ${item.currency}`}</Typography.Paragraph>
            {item.receipt_source ? <Typography.Paragraph type="secondary" className="mb-1">账单依据：{item.receipt_source} {item.revisions?.length ? `· 更正记录 ${item.revisions.length} 次` : ""}</Typography.Paragraph> : null}
            {editing === item.task_id ? <Space direction="vertical" className="w-full">
                <Space><InputNumber min={0} max={1000000} value={amount} onChange={setAmount} placeholder="实际金额" /><Input value={currency} onChange={(event) => setCurrency(event.target.value)} placeholder="原始单位，如 CNY 或积分" /></Space>
                <Input value={receipt} onChange={(event) => setReceipt(event.target.value)} placeholder="供应商账单出处或核对编号" />
                <Space><Button type="primary" disabled={busy || amount == null || !currency.trim() || !receipt.trim()} onClick={() => void act(async () => { await studioApi.settleCost(project.id, item.task_id, amount!, currency, receipt); setEditing(undefined); })}>保存实付</Button><Button onClick={() => setEditing(undefined)}>取消</Button></Space>
            </Space> : <Button type="link" disabled={busy} onClick={() => startEditing(item)}>{item.actual == null ? "按供应商账单登记" : "更正实付"}</Button>}
        </div>)}
    </section>;
}
