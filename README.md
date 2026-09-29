# 广告电商创作工作台

Windows 本地单人版。克隆仓库后将 `.env.example` 复制为 `.env` 并填写自己的密钥，再双击 `启动工作台.bat`；浏览器将打开 `http://127.0.0.1:8765`。需要 Python 3.11+、Pillow 和 FFmpeg；当前开发机已具备这些依赖。无需安装 npm 依赖。

项目状态存于 `data/workbench.sqlite3`，原始文件存于 `data/files`。`data` 不应同步至公共仓库。完整备份可从交付页下载并恢复。DeepSeek 与 SeeAny 密钥从项目根目录 `.env` 读取，也兼容本机 `data/provider-settings.json`；密钥不包含在项目备份中，也不会从设置接口回显。

当前实现的是可离线工作的项目、来源文件、事实确认、不可覆盖简报、母版审核、创意与分镜审批、提示词版本、资产挑选、套图文字层、画布关系、费用阻止、任务登记、项目备份与交付包。导入 Flova 视频素材后，可在本机裁剪、排序、变速、添加字幕并导出竖屏 MP4。套图 PNG 和 SVG 会使用同一底图与文字内容，SVG 的文字层可编辑。

DeepSeek 文本提取、三个广告方向、聊天建议及字段化提示词修改使用 **DeepSeek-V4.1-Flash**，官方 API 模型 ID 为 `deepseek-flash`。在项目根目录 `.env` 填写 `DEEPSEEK_API_KEY=` 后的值，再到“任务与费用”填写当前输入/输出单价与 AI 阶段预算；每次调用仍须先查看估算，再单独批准。模型候选事实始终为“待核实”，用户对照原件后才能改为已知事实并确认。图片观察暂未启用，因为图片 token 费用无法用当前文本上界可靠估算。

SeeAny API 通用 Skill 已安装在 `skills/seeany-api`，并接入本机 Codex 的 Skill 目录。工作台网页现已提供三视图候选、单张试图、套图策划与正式套图的 SeeAny 提交入口及手动状态同步。视频页已接入已安装并登录的 Flova CLI：可创建或关联项目、同步项目、发送创作要求、恢复运行状态、检查导出条件及导出视频。Jev 旁路判断尚未启用。SeeAny 生成价格仍须在实际账号下核对，工作台不会把本地登记任务当作远端已执行任务。

## 网页中的 Flova CLI

进入“视频收尾”，点击“创建 Flova 项目”，或在“Flova 项目”表单关联已有项目 ID。填写本轮创作要求后，明确点击“确认发送并等待结果”。CLI 使用已登录的本机账号等待本轮完成；如果网络中断或返回不明，先点“恢复运行状态”，避免重复发送。导出前先点“检查导出条件”，确认可导出后再点“确认导出 Flova 成片”。Flova 返回的待确认操作和素材真实性仍须人工审核；需要本机收尾时，可把生成视频导回页面的镜头列表。

## 网页中的 SeeAny 生成

打开项目根目录 `.env`，在 `SEEANY_API_KEY=` 后填写[SeeAny 开发者 API Key](https://www.seeany.com/developer/keys)，保存后刷新工作台网页。此文件已加入 `.gitignore`，不进入项目备份；不要把密钥发到聊天或写进提示词。系统会在每次请求时读取该文件，无需重启工作台。

1. 先导入商品图片、逐项确认事实并建立简报。在“三视图母版”页面选择参考图、候选组和 SeeAny 的 `views` 预设，核对费用后提交。官方文档示例为 `front`；其他预设请按 SeeAny 当前说明填写。任务完成后在“任务与费用”同步结果，生成图会标为含推断的候选，仍需人工核对正面、侧面、背面。
2. 母版批准后，可在“提示词与选图”选择版本生成一张试图；也可在“电商套图”先使用策划接口，再编辑并批准方案，最后正式出图。策划接口当前文档价格为 ¥0.10/次；其余生成操作需要你先依据供应商当前价格填写预计费用。费用未知或超过阶段/总预算时无法提交。
3. 远端异步任务只在“任务与费用”页手动同步。完成的图片会下载到本地候选资产，仍需真实性审核；网络中断或状态不明时不会自动重发。预计费用在核对并记录实际扣费前持续占用预算。

本功能的请求字段依据 [SeeAny 公开 API 文档](https://www.seeany.com/developer/api-docs.json) 中能力 464、113、72373、2373。已配置本机密钥，但尚未执行计费烟测；首次使用请先以单张低费用任务验证模型、预设、状态返回和实际扣费。

## SeeAny 公开接口

在本项目目录打开 PowerShell。Windows 上使用 `python -X utf8`，避免接口名称中的字符被系统默认编码截断。下面的脚本每次从 [SeeAny 在线接口文档](https://www.seeany.com/developer/api-docs.json) 读取当前目录；2026-09-29 验证时有 31 个能力。

```powershell
$seeany = '.\skills\seeany-api\scripts\seeany_api.py'
python -X utf8 $seeany catalog                   # 全部文档化能力
python -X utf8 $seeany catalog --query '电商'   # 按名称、分组、描述、ID 或路径搜索
python -X utf8 $seeany describe 2373            # 查看完整参数、模型、注意事项和示例
```

调用前在 [SeeAny 密钥页](https://www.seeany.com/developer/keys) 创建 API Key，并只在当前终端会话中设置 `SEEANY_API_KEY`。不要把密钥写进请求 JSON 或仓库。按 `describe` 返回的实时参数准备 `request.json`，再提交；生成接口可能扣费。

```powershell
$secret = Read-Host 'SeeAny API Key' -AsSecureString
$env:SEEANY_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
python -X utf8 $seeany call 2373 --payload-file .\request.json
python -X utf8 $seeany upload .\product.png
python -X utf8 $seeany call --method GET --path /api/developer/task/status --payload-file .\task-query.json
python -X utf8 $seeany call --method GET --path /api/developer/balance
```

`task-query.json` 可写为 `{"task_uuid":"实际返回的任务 ID"}`。目录之外还有文档列出的公共接口：`POST /api/upload/image`（用 `upload`）、`POST /api/prompt-tools/ai-refine`、`GET /api/developer/task/status` 和 `GET /api/developer/balance`。Skill 的完整说明见 `skills/seeany-api/SKILL.md`。工作台设置页中的 SeeAny 密钥与此脚本的环境变量独立，当前网页还不会自动提交 SeeAny 任务。

真实商品验收还需要用户提供小家电实拍、已知规格、供应商凭证及拼多多商家后台的实际上传规格。
