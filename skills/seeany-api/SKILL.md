---
name: seeany-api
description: 发现并调用 SeeAny 开发者 API 的通用 Skill。用于查找图片、视频、音频、3D、电商套图等能力，查看实时请求参数，上传图片，提交任意文档化接口，或查询异步任务状态。当用户提到 SeeAny API、api.seeany.com、/developer/docs、API Key 或希望 AI 直接调用 SeeAny 能力时使用。
---

# SeeAny API

使用 `scripts/seeany_api.py` 读取线上开发文档并调用接口。不要凭记忆猜测能力 ID、路径或参数。

## 调用流程

1. 运行 `catalog` 搜索合适能力。
2. 运行 `describe` 读取该能力的当前参数、模型、注意事项和示例。
3. 仅在参数齐全后运行 `call`。调用可能产生费用时，先向用户明确最终参数；若用户已明确要求立即执行，则直接调用。
4. 响应含 `task_uuid` 时，按用户需求查询 `/api/developer/task/status`。终态为 `succeeded`、`partial_failed` 或 `failed`。

## 鉴权与素材

- API Key 优先读取 `SEEANY_API_KEY`，也可使用 `--api-key`。若没有，请用户前往 `https://www.seeany.com/developer/keys`。
- 不要将 API Key 写入仓库、请求 JSON 或输出。
- 请求必须带 `User-Agent`；脚本默认使用 `seeany-api`。
- 本地图片先用 `upload` 上传，再将返回 URL 放入 `inputImgs` 等文档指定的字段。
- 只使用用户提供或明确授权的素材，不要擅自搜索替代图片。

## 命令

```bash
# 列出或搜索能力
python3 scripts/seeany_api.py catalog
python3 scripts/seeany_api.py catalog --query "视频"

# 用 aiTypeId、名称或 endpoint id 查看详情
python3 scripts/seeany_api.py describe 135

# 调用某个能力；脚本会根据文档补全 aiTypeId/aiType
python3 scripts/seeany_api.py call 113 --payload-file request.json

# 调用公共路径，GET 参数也从 JSON 读取
echo '{"task_uuid":"wtask_xxx"}' | python3 scripts/seeany_api.py call --method GET --path /api/developer/task/status

# 查询 API 账户余额
python3 scripts/seeany_api.py call --method GET --path /api/developer/balance

# 上传本地图片
python3 scripts/seeany_api.py upload ./product.png
```

`call` 默认从 stdin 读 JSON，也可传 `--payload-file`。套图策划等 SSE 接口会直接输出事件流。不要对文档未列出的路径做探测性调用。

文档中不属于能力目录的公共接口有：`POST /api/upload/image`、`POST /api/prompt-tools/ai-refine`、`GET /api/developer/task/status` 和 `GET /api/developer/balance`。上传使用 `upload`，其余使用 `call --method ... --path ...`。
