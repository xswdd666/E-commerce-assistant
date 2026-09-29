import { useState } from "react";
import { Button, Input, InputNumber, Select, Space, Tag, Typography } from "antd";

import { studioApi, type StudioCost, type StudioProject } from "@/services/api/commerce-studio";

type Props = { project: StudioProject; busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void>; nodeNames: Record<string, string> };

export function CommerceCostSection({ project, busy, act, nodeNames }: Props) {
    const [editing, setEditing] = useState<string>();
    const [amount, setAmount] = useState<number | null>(null);
    const [currency, setCurrency] = useState("CNY");
    const [receipt, setReceipt] = useState("");
    const [purpose, setPurpose] = useState<string>("全部");
    const [node, setNode] = useState("全部");
    const [dimension, setDimension] = useState("provider");
    const [targetEditing, setTargetEditing] = useState(false);
    const [targetAmount, setTargetAmount] = useState<number | null>(null);
    const [targetCurrency, setTargetCurrency] = useState("CNY");
    const target = project.cost_target;
    const targetSpent = target ? project.costs.reduce((sum, item) => sum + (item.currency === target.currency && item.actual != null ? item.actual : 0), 0) : 0;
    const visible = project.costs.filter((item) => (purpose === "全部" || item.purpose === purpose) && (node === "全部" || (item.node_id || "项目级") === node));
    const totals = new Map<string, { actual: number; estimate: number; estimated: number; pending: number }>();
    const groups = new Map<string, { label: string; currency: string; actual: number; estimate: number; estimated: number; pending: number }>();
    for (const item of visible) {
        const currency = item.currency || "单位未知";
        const total = totals.get(currency) || { actual: 0, estimate: 0, estimated: 0, pending: 0 };
        if (item.actual == null) total.pending++;
        else total.actual += item.actual;
        if (item.estimate != null) { total.estimate += item.estimate; total.estimated++; }
        totals.set(currency, total);
        const label = dimension === "node" ? (item.node_id ? nodeNames[item.node_id] || item.node_id : "项目级")
            : dimension === "date" ? (item.submitted_at ? new Date(item.submitted_at).toLocaleDateString("zh-CN") : "日期未知")
            : String(item[dimension as "provider" | "stage" | "purpose"] || "未知");
        const key = JSON.stringify([label, currency]);
        const group = groups.get(key) || { label, currency, actual: 0, estimate: 0, estimated: 0, pending: 0 };
        if (item.actual == null) group.pending++;
        else group.actual += item.actual;
        if (item.estimate != null) { group.estimate += item.estimate; group.estimated++; }
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
        <Typography.Paragraph>成本目标：{target ? `${target.amount.toFixed(2)} ${target.currency}；同单位已确认实付 ${targetSpent.toFixed(2)}${targetSpent > target.amount ? "，已超过目标" : ""}` : "未设置"}。仅供参考，不阻止创作提交。</Typography.Paragraph>
        {targetEditing ? <Space wrap><InputNumber min={0} value={targetAmount} onChange={setTargetAmount} placeholder="目标金额" /><Input value={targetCurrency} onChange={(event) => setTargetCurrency(event.target.value)} placeholder="原始单位" /><Button type="primary" disabled={busy || targetAmount == null || !targetCurrency.trim()} onClick={() => void act(async () => { await studioApi.setCostTarget(project.id, targetAmount, targetCurrency); setTargetEditing(false); })}>保存目标</Button><Button onClick={() => setTargetEditing(false)}>取消</Button></Space>
            : <Space><Button disabled={busy} onClick={() => { setTargetAmount(target?.amount ?? null); setTargetCurrency(target?.currency || "CNY"); setTargetEditing(true); }}>{target ? "修改目标" : "设置成本目标"}</Button>{target ? <Button disabled={busy} onClick={() => void act(() => studioApi.setCostTarget(project.id, null, ""))}>清除目标</Button> : null}</Space>}
        <Typography.Paragraph>共 {visible.length} 笔任务；{visible.filter((item) => item.actual == null).length} 笔待结算；{visible.filter((item) => item.estimate == null).length} 笔预估未知。预估与实付分开计算，币种和积分不混加。</Typography.Paragraph>
        {[...totals].map(([currency, total]) => <Typography.Paragraph key={currency} className="mb-1">{currency}：已确认实付 {total.actual.toFixed(2)}；已知预估 {total.estimated ? total.estimate.toFixed(2) : "未知"}；待结算 {total.pending} 笔</Typography.Paragraph>)}
        <Space wrap><Select value={purpose} onChange={setPurpose} options={["全部", "草稿", "正式"].map((value) => ({ value, label: value }))} /><Select value={node} onChange={setNode} options={[{ value: "全部", label: "全部节点" }, ...[...new Set(project.costs.map((item) => item.node_id || "项目级"))].map((value) => ({ value, label: value === "项目级" ? value : nodeNames[value] || value }))]} /><Select value={dimension} onChange={setDimension} options={[{ value: "provider", label: "按供应商" }, { value: "stage", label: "按阶段" }, { value: "node", label: "按节点" }, { value: "purpose", label: "按用途" }, { value: "date", label: "按提交日期" }]} /></Space>
        {[...groups.values()].map((group) => <div key={JSON.stringify([group.label, group.currency])} className="mt-2"><Tag>{group.label}</Tag>已确认 {group.actual.toFixed(2)} {group.currency} · 已知预估 {group.estimated ? group.estimate.toFixed(2) : "未知"} · 待结算 {group.pending} 笔</div>)}
        {visible.map((item) => <div key={item.id} className="mt-3 border-t pt-2">
            <Typography.Text><Tag>{item.provider}</Tag>{item.stage} · {item.purpose} · {item.status}</Typography.Text>
            <Typography.Paragraph className="mb-1">节点：{item.node_id ? nodeNames[item.node_id] || item.node_id : "项目级"}；提交：{item.submitted_at ? new Date(item.submitted_at).toLocaleString("zh-CN") : "未知"}<br />预计：{item.estimate == null ? "未知" : `${item.estimate} ${item.currency || "单位未核实"}`}；实付：{item.actual == null ? "待结算" : `${item.actual} ${item.currency}`}</Typography.Paragraph>
            {item.receipt_source ? <Typography.Paragraph type="secondary" className="mb-1">账单依据：{item.receipt_source} {item.revisions?.length ? `· 更正记录 ${item.revisions.length} 次` : ""}</Typography.Paragraph> : null}
            {editing === item.task_id ? <Space direction="vertical" className="w-full">
                <Space><InputNumber min={0} max={1000000} value={amount} onChange={setAmount} placeholder="实际金额" /><Input value={currency} onChange={(event) => setCurrency(event.target.value)} placeholder="原始单位，如 CNY 或积分" /></Space>
                <Input value={receipt} onChange={(event) => setReceipt(event.target.value)} placeholder="供应商账单出处或核对编号" />
                <Space><Button type="primary" disabled={busy || amount == null || !currency.trim() || !receipt.trim()} onClick={() => void act(async () => { await studioApi.settleCost(project.id, item.task_id, amount!, currency, receipt); setEditing(undefined); })}>保存实付</Button><Button onClick={() => setEditing(undefined)}>取消</Button></Space>
            </Space> : <Button type="link" disabled={busy} onClick={() => startEditing(item)}>{item.actual == null ? "按供应商账单登记" : "更正实付"}</Button>}
        </div>)}
    </section>;
}
