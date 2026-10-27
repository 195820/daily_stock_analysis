# ntfy 双向通道：接入与多项目复用

> 状态：2026-10-27 落地并跑通（服务器实测）。本文既是 DSA 的接入说明，也是**其它项目复用同一通道**的对接文档。

## 1. 为什么选 ntfy

服务器出网实测（阿里云 cn-shanghai）：

| 通道 | 实测 | 结论 |
|---|---|---|
| `api.telegram.org`（IPv4 `202.160.130.145` / 无全局 IPv6） | TCP 超时 | ✗ |
| `web.telegram.org`、`discord.com`、`matrix.org`、`api.line.me` | `000` | ✗ |
| Cloudflare WARP `engage.cloudflareclient.com:2408` | 阻塞 | 无法用 WARP 套隧道 |
| **`ntfy.sh`** | 发布 200 / 订阅 200 / 28 KB 附件 200 | ✓ |
| 钉钉、飞书、Server酱、PushPlus | 可达 | 但内容经企业审计/平台审核 |
| PushDeer、Bark、Gotify | 可达（Gotify 需自建） | 单向，无指令回传 |

ntfy 是唯一同时满足「境内可达 + 无内容审核 + 双向 + 可自建 + 按 topic 分项目」的选项。

## 2. 两种拓扑

### 2.1 单 topic 对话流（DSA 现用）

```
NTFY_URL     = https://ntfy.sh/<随机串>         # 出站：报告/回复
NTFY_CMD_URL = https://ntfy.sh/<随机串>         # 入站：与 NTFY_URL 同值即对话流
NTFY_STREAM_ENABLED = true
NTFY_TOKEN   = <可选，自建或受保护 topic 需要>
```

收发同流时靠三道闸门互不干扰：

1. **机器标记**：所有机器产出一律打 `robot` tag，订阅端见 tag 立即跳过（兼容早期 `bot` tag）。
2. **可解析性**：与 `CommandDispatcher` 同一套解析（`/cmd` 或中文命令词）才算「在叫机器人」；未命中的短文本保留 NL 路由但 `mentioned=False`（不触发问候），超过 200 字符 / 10 行直接丢弃。
3. **附件占位**：ntfy 对纯附件自动填 `You received a file: xxx`，图片一律按 `/portfolio` 处理，原文进 `raw_data.caption`。

### 2.2 收发分离（多端只读 / 可分别静音）

`NTFY_CMD_URL = https://ntfy.sh/<随机串>-cmd`，与出站 topic 分开。适合「报告给多人只读、指令只给自己」。

## 3. 协议与限额（对接其它项目必须知道的数字）

| 项 | 值 | 影响 |
|---|---|---|
| 单条 `message` 上限 | **4096 字节**（服务端默认 `message-size-limit`） | 中文约 **1300 字/条**；本仓库阈值取 **3800 B** 分段，中文回复多时会更碎 |
| 超长正文 | 同一次 `PUT <server>/<topic>` 携带 `Filename` 头（ASCII）+ `?title=&message=` query 参数（URL 编码） | 用户侧**一条消息 = 结论 + 附件**；`NtfySender` >3.8 KB 自动走此路径，仍失败才分段 |
| 公共 ntfy.sh 附件 | 2 MB/次、20 MB/visitor、**3 小时过期** | 28 KB 的 `reasoning.md` 无压力 |
| 附件 MIME | 服务端按**内容嗅探**判定（Go `http.DetectContentType`），请求的 `Content-Type` 与 `Filename` 扩展名都不参与（官方示例注释 `// Does not matter`） | `.md` 正文一律存成 `text/plain; charset=utf-8`，存储 URL 后缀变 `.txt`；`Content-Disposition` 仍为 `filename="xxx.md"`，文件名与安卓侧识别不受影响；`text/markdown` 直传不可能 |
| 公共 ntfy.sh 消息量 | 250 条/日、200 MB/日、30 个并发订阅 | 多项目共用公共实例要注意配额 |
| keepalive | 45 秒一次 | 订阅读超时须 > 45 s（本实现 90 s） |
| 订阅协议 | `GET <server>/<topic>/json` NDJSON（也有 `/sse`、`/ws`） | 长连接 + 断线用 `since=<最后 id>` 续传 |
| 首连语义 | `since=<进程启动时间戳>` | **不重放历史指令**（重放会重复执行，`/analyze` 之类会白烧 LLM） |

## 4. 可复用代码（换项目只换 topic）

| 文件 | 职责 |
|---|---|
| `src/notification_sender/ntfy_sender.py` | 出站：≤3800 B 直发；超限把 `summary` 当消息正文与附件同一次 PUT 发布（单条气泡）；上传失败按字节分段（CJK 按字符硬切）；头部只放 ASCII 文件名（中文标题走 query） |
| `bot/platforms/ntfy_stream.py` | 入站：长连接订阅 → `BotMessage` → `CommandDispatcher` → 回帖；含回声过滤、`since` 续传、附件下载（图片 ≤15 MB）、`stop()` 关流 |
| `main.py --bot` | 只起 Stream/ntfy 订阅，不跑分析、不起 Web；无可用订阅时 **exit 1**（避免僵尸服务） |
| `deploy/systemd/dsa-bot.service` | `Type=simple`、`MemoryMax=700M`、`Restart=on-failure`；空闲 RSS 约 80 MB |

新项目接入最小清单：

1. 生成随机 topic（≥20 位），必要时再建 `-cmd`；
2. `.env` 填 `NTFY_URL` / `NTFY_CMD_URL` / `NTFY_STREAM_ENABLED=true`（自建再加 `NTFY_TOKEN`）；
3. 起一个 `--bot` 形态的常驻 unit（或复用现有 dispatcher 加自己的命令类）；
4. 出站调用 `NtfySender.send_to_ntfy(text, title)`；
5. 业务侧若需「先预览再落库」，参考 `bot/commands/portfolio.py` 的 pending 队列（落盘 + TTL + confirm/cancel）。

## 5. 多项目共用时的两种形态

| 形态 | 做法 | 代价 |
|---|---|---|
| **多进程**（当前默认，零改动） | 每项目一个 unit，指向同一 ntfy 服务端的不同 topic | 每个常驻进程约 80 MB 空闲 |
| **单进程多 topic**（需扩展） | `NTFY_CMD_TOPICS` 逗号列表 + `cmd_topic → (out_topic, dispatcher)` 路由 | 约 60–80 行改动，不动协议；适合常驻进程超过 3–4 个时再合并 |

## 6. 排错对照表

| 现象 | 根因 | 处理 |
|---|---|---|
| App 报 `WebSocket upgrade failed with HTTP 404` | **Server 字段被填成整条 topic URL**（App 拼出 `/<topic>/<topic>/ws`） | Server 只填 `https://ntfy.sh`，Topic 只填主题名 |
| 发 `/portfolio confirm` 回「没有待确认」 | 待确认队列原为纯内存，服务重启即丢 | 已改为 `data/pending_portfolio.json` 落盘（`PORTFOLIO_PENDING_PATH` 可覆盖） |
| 推 5 KB 纯文本返回 HTTP 500 | 超出 4096 字节单条上限 | 走附件路径（>3.8 KB 自动） |
| 重启后旧指令被执行了一遍 | 首连用了 `since=15m` | 已改为进程启动时间戳锚定 |
| 机器人回复自己触发的问候 | 出站未打机器标记 | 统一打 `robot` tag，订阅端跳过 |
| iOS 自建服务器通知延迟数小时 | iOS 后台只能靠 APNs，自建需转发 | 服务端配 `upstream-base-url: https://ntfy.sh`（ECS 出网可达） |

## 7. 自建 ntfy（需要更强的隔离时）

- 公共 ntfy.sh 的 topic 名即凭据（40+ 随机串），内容经第三方主机（德国），**无内容审核但不自持**。
- 自建：`apt install ntfy`（Ubuntu 源内 2.11.x，无需 GitHub），配 `auth-default-access: deny-all` + access token + 附件目录 + `cache-file`。
- 需要手机直连时要在安全组放行一个端口；iOS 即时推送仍需 `upstream-base-url`。
- 内存开销约 20–30 MB（Go 单二进制），对 1.7 GB 机器可接受。
