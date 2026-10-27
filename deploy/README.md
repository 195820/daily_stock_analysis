# ECS 部署（GOT 跑批 + 推送，无 WebUI）

单机部署（无 Docker），代码 / 数据 / 配置三区分离：

```
/opt/dsa/code      # 仓库代码（含 .env，600 dsa:dsa）
/opt/dsa/data      # stock_analysis.db + got_daily/ + aggregator_contexts/ + got_timeseries/
/opt/dsa/logs      # LOG_DIR（logrotate 目标）
/opt/dsa/venv      # python3 -m venv，pip 走 mirrors.aliyun.com
```

2026-10-24 用户裁决「不做 WebUI，只跑 GOT 并推送」后：`dsa-web` / `dsa-schedule` 两个常驻服务已移除，
前端产物（`static/`）与 `apps/dsa-web/node_modules/` 已删除（回收约 310 MB），改由 **systemd timer 定时跑 GOT**。

### 当前状态（别误判）

| 能力 | 状态 |
|---|---|
| 每工作日 13:20 跑完整 GOT（Phase 1+2）+ 写归档 | ✅ 已生效（`dsa-got.timer`，NEXT 见 `systemctl list-timers`） |
| GOT 报告推送到 IM | ❌ 未生效：`.env` 无真实通道，`Config.has_notification=false`；**工作日 13:20 会跑、会归档**（推送取决于通道配置） |
| 消息里发持仓截图自动更新持仓 | ❌ 未接：`bot/platforms/feishu_stream.py:413-415` 仍丢弃非文本消息；OCR/解析/对账三层已就绪（见下节），当前只能「给图片路径 → 拿持仓与调仓预览」 |
| 手动给一张截图得到识别结果 | ✅ 可用（`ingest_path`，服务器实测 3 只持仓） |

推送通道必须选**自建应用 + 长连接**（webhook 类只能推、收不到图）；凭据到位后再装 `dsa-bot` 常驻服务。
长连接期间掉线**不补发**：飞书只在连接存活时投递事件，进程停掉的图片不会重投——回执里会带「未收到回执请重发」。

## 必须做的一步：data 软链

GOT 归档/跨日记忆/时序目录在代码里是**相对路径**（`data/got_daily`、`data/aggregator_contexts`、
`data/got_timeseries`；`src/got/archive.py:_default_base_dir` 由 `__file__` 推算到 `<repo>/data`）。
不建软链时进程会在 `/opt/dsa/code/data/` 另起一套空树，跨日记忆与 EMA 基线全部丢：

```bash
ln -s /opt/dsa/data /opt/dsa/code/data
```

数据库走绝对路径，见 `.env` 的 `DATABASE_PATH`。

## 定时任务：dsa-got.timer

| 文件 | 作用 |
|---|---|
| `deploy/systemd/dsa-got.service` | `Type=oneshot`，`ExecStart=/opt/dsa/venv/bin/python main.py --got`，`TimeoutStartSec=3600`，`MemoryMax=1000M`（为将来 `dsa-bot` 常驻的 ~330 MB 预留） |
| `deploy/systemd/dsa-got.timer` | `OnCalendar=Mon..Fri 13:20`（盘中快照，`bar_complete=false`），**不设 `Persistent`**（错过就跳过，避免开机补跑在任意时点用不完整 bar 出决策） |

```bash
install -m 644 deploy/systemd/dsa-got.service deploy/systemd/dsa-got.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now dsa-got.timer
systemctl list-timers dsa-got.timer          # 期望 NEXT = 下个工作日 13:20
systemctl start dsa-got.service              # 立即跑一次（非交易日会正常跳过）
journalctl -u dsa-got.service -n 30 --no-pager
```

语义说明：

- 只按工作日触发；**A 股节假日由 GOT 内部 `is_market_open()` 跳过并以 exit 0 结束**（`main.py:1642-1645`），
  所以 timer 不需要自己维护节假日表。
- 15:30 前手动跑也能跑，只是归档标 `bar_complete=false`，被 P6 兑现追踪当盘中快照分组；要干净归档就收盘后跑。
- 旧方案的 `main.py --schedule` 跑的是「选股 + 常规分析」链，**不含 GOT**，与本次目标不符，已下线。

## 推送（当前未配置，必须补）

**目标已升级为双向**：既要推 GOT 报告，也要收「持仓截图」更新持仓。只有**自建应用 + 长连接（Stream/WebSocket）**能同时满足——
webhook 类通道只能推、收不到消息，且需要公网回调（本机只放 22，没这条路）。

| 方案 | 推送 | 收图 | 公网要求 |
|---|---|---|---|
| 飞书自建应用 + 长连接事件订阅 | ✓ | ✓ | 无（出网 WebSocket） |
| 钉钉自建应用 + Stream 模式 | ✓ | ✓ | 无（出网 WebSocket） |
| 各类 webhook（飞书/钉钉自定义机器人、PushPlus、Server酱） | ✓ | ✗ | — |

飞书方案需要：

1. `FEISHU_APP_ID` / `FEISHU_APP_SECRET`（真实值，现有的是 `xxxx` 占位）；
2. 应用权限：`im:message`（发消息）、`im:resource`（下载图片资源）；
3. 事件订阅 `im.message.receive_v1`，订阅方式选 **长连接**；
4. `FEISHU_CHAT_ID`（推送目标会话）、`FEISHU_RECEIVE_ID_TYPE=chat_id`；
5. `.env` 里 `FEISHU_STREAM_ENABLED=true`（收消息的客户端开关）。

当前 `src/config.py` 的 `has_notification` 为 false，GOT 跑完不推送；
`bot/platforms/feishu_stream.py:413-415` 还会显式丢弃非文本消息，收图分支需随凭据一起接。

配好后先验推送链路：

```bash
cd /opt/dsa/code && sudo -u dsa /opt/dsa/venv/bin/python -c "
from src.notification import get_notification_service
print('send ok =', get_notification_service().send(content='DSA 推送自测', severity='info'))
"
```

GOT 自身推送在 `main.py:1598-1635`：`GOT_NOTIFY_ENABLED`（缺省 1）且 Phase 2 `status != failed` 时发送，
`dedup_key=got_daily_<日期>`。**去重是进程内的**（`src/notification_noise.py` 的 `_dedup_expires_at` 是模块级字典，注释写明 process-local），而每次 `main.py --got` 都是新进程 —— 所以「当天手动跑一次 + 13:20 timer 再跑一次」会收到**两条**推送；要只收一条就别在定时之外重跑。

## 持仓截图 OCR（已可用，2026-10-24 实测）

- 后端：`rapidocr-onnxruntime`（ONNX 版 PP-OCR，模型随包、离线可跑）。`paddlepaddle` 在 py3.14 无 wheel，
  装了 paddle 会优先用、否则自动退 rapidocr（`PortfolioOcrImportService._load_backend()`）。
- 系统依赖：`apt install -y libgl1`（opencv 需要）；`fonts-noto-cjk` 只有自造测试图时才需要。
- 解析：`parse_holdings_from_boxes()` 按版面取列，**不再用「名称 数量 成本 现价」紧邻正则**
  （那条正则会拿 6 位代码当数量 → 1 股、成本变 1000）；`compute_sync_trades()` 双侧归一化代码格式
  （持仓表存裸码 `600519`，`match_stock_name()` 返回 `SH600519`）。
- 自检命令（用任意持仓截图替换路径）：

```bash
cd /opt/dsa/code && sudo -u dsa env PYTHONPATH=/opt/dsa/code /opt/dsa/venv/bin/python -c "
from src.services.portfolio_screenshot_ingest import PortfolioScreenshotIngestService
svc = PortfolioScreenshotIngestService()
res = svc.ingest_path('/tmp/holdings.png')
print(res.ok, len(res.holdings)); print(res.summary)
"
```

- 默认只算不改库（`build_reply` 给预览）；真正落库要显式调用 `PortfolioService.record_trade`。

## `.env` 关键键

```
DATABASE_PATH=/opt/dsa/data/stock_analysis.db
LOG_DIR=/opt/dsa/logs
SCHEDULE_ENABLED=true
SCHEDULE_TIME=18:00   # 仅遗留 `--schedule` 路径使用；`dsa-schedule` 已下线，GOT 由 dsa-got.timer(13:20) 驱动
SCHEDULE_RUN_IMMEDIATELY=false
GOT_NOTIFY_ENABLED=1                      # GOT Phase 2 推送开关（缺省即 1）
LITELLM_LOCAL_MODEL_COST_MAP=True         # GitHub 不可达，跳过 litellm 远程价格表重试（3 次 × ~2s）
<推送通道键>                               # 见上表，必须真值
```

`WEBUI_ENABLED=false`、`WEBUI_HOST`、`WEBUI_AUTO_BUILD` 在无 WebUI 部署下已无作用（留着无害）；
`API_PORT` 本仓不读，属死键。`SCHEDULE_*` 现在也不驱动任何常驻进程（`--schedule` 服务已下线），
保留是为了 `main.py` 无参运行时仍按 18:00 的分支语义走。

## requirements-core.txt

服务器上 `requirements.txt` 保持原样，装机时生成可用子集（去掉 GitHub 的 alphasift 与 Python 3.14 无 wheel 的 OCR 包）：

```bash
grep -vE '^(paddlepaddle|paddleocr|git\+)' requirements.txt > requirements-core.txt
sudo -u dsa /opt/dsa/venv/bin/pip install -r requirements-core.txt
python -m playwright install chromium     # 问财选股要用；只跑 GOT 可不装
```

## 手动跑批与自检

```bash
cd /opt/dsa/code && sudo -u dsa /opt/dsa/venv/bin/python main.py --got     # 无 WebUI 占内存，不必先停服务

D=$(date +%Y%m%d)
ls /opt/dsa/data/got_daily/$D | wc -l                                     # 期望 ≥23
for d in /opt/dsa/data/got_daily/[0-9]*; do ... done                      # 空壳日探测，期望无输出
python -c "import json;d=json.load(open('/opt/dsa/data/got_daily/$D/sector.json'));print(d['data_quality']['board_fetch_stats'])"
#   ↑ 期望 {'total': 212, 'success': 212, 'failed': 0}
python -c "import json;d=json.load(open('/opt/dsa/data/got_daily/$D/portfolio_decision.json'));print(d['status'])"
#   ↑ 期望 normal（failed 表示当日无决策，属失败退出）
```

追补历史日期只能走 `GOTOrchestrator(use_mock=False).run(check_date=...)`。注意
`src/got/nodes/news_analysis.py:1088` 的 Node 4 影子产物用 `date.today()` 而非 `run_date`，
会额外生成 `<今天>` 目录（仅 `shadow_low_events.json`）；追补后删掉该目录，否则被子目录解析当成交割日。

## 远端现状快照（采集于 2026-10-24 15:05 CST）

> 数字是采集时点的，重装 / 升级 / 跑批后都会漂。每块末尾给了对应采集命令，核对时重跑那几条。

### 实例与系统

| 项 | 值 |
|---|---|
| 实例 / 区域 | `<ECS 实例ID>`（见控制台）/ cn-shanghai |
| 公网入口 | `<ECS 公网IP>`；采集时仅 22 实测可达 |
| OS / 内核 | Ubuntu 26.04.1 LTS / `7.0.0-31-generic` |
| CPU / 内存 | 2 vCPU / 1735 MB + 2 GB swap（`vm.swappiness=10`、`vfs_cache_pressure=50`、`somaxconn=1024`） |
| 磁盘 | 40 GB；采集时已用 23%（以 `df -h /` 为准） |
| 时区 | Asia/Shanghai (CST)，`System clock synchronized: yes` |

```bash
. /etc/os-release; echo $PRETTY_NAME; nproc; free -m | sed -n 2p; df -h / | tail -1
swapon --show; sysctl -n vm.swappiness net.core.somaxconn
```

### 运行时

| 项 | 值 |
|---|---|
| 系统 Python | `/usr/bin/python3` 3.14.4（仅建 venv / 一次性脚本） |
| 业务 Python | `/opt/dsa/venv/bin/python` 3.14.4，pip 26.2.2 |
| pip 源 | `https://mirrors.aliyun.com/pypi/simple/`（`trusted-host` 同名） |
| Node / npm | v22.22.1 / 9.2.0（仅前端构建；当前未用） |
| git / gcc | 2.53.0 / 15.2.0 |
| 浏览器 | playwright 1.64.1 + `chromium-1248`（`/home/dsa/.cache/ms-playwright`），仅问财选股用 |

### 关键包实测版本

`requirements.txt` 给的是区间，以下是 pip 实际解出的版本（`pip list` 复测）：

| 包 | 远端 | 工作站 | 差异说明 |
|---|---|---|---|
| pandas | **3.1.0** | 2.3.3 | Python 3.14 无 2.x wheel，无法对齐 |
| numpy | **2.5.4** | 2.2.6 | 同上 |
| mootdx / tdxpy | 0.11.7 / 0.2.7 | 同 | TDX TCP 取数（板块 K 线/stock_all）必需 |
| litellm | 1.82.6 | 同 | 必须 `<1.83`：mootdx 钉 `httpx>=0.25,<0.26` |
| httpx | 0.25.2 | 同 | 同上 |
| tiktoken | **0.13.0** | 0.11.0 | `<0.12` 无 cp314 wheel（sdist 需 Rust） |
| playwright | 1.64.1 | 1.61.0 | 版本自洽，浏览器随包下载 |
| akshare / efinance | 1.19.3 / 0.5.9 | 1.18.54 / 0.5.8 | — |
| openai | 2.54.0 | 2.31.0 | litellm 传递依赖，未钉版本 |
| SQLAlchemy / fastapi / uvicorn | 2.1.6 / 0.161.1 / 0.54.0 | 2.0.49 / 0.135.3 / 0.44.0 | `>=` 区间解出的最新 |

未安装且功能相应关闭：`alphasift`（GitHub 不可达）、`paddlepaddle`/`paddleocr`（无 cp314 wheel，OCR 导入不可用）。

```bash
/opt/dsa/venv/bin/pip list --format=freeze | grep -iE '^(pandas|numpy|mootdx|litellm|httpx|tiktoken|playwright)='
```

### 服务

| unit | 形态 | enabled / active |
|---|---|---|
| `dsa-got.timer` | 工作日 13:20 触发 `dsa-got.service` | enabled / active |
| `dsa-got.service` | oneshot `main.py --got`，MemoryMax 1000M | 按需，无触发时 inactive |

无 WebUI：8000 端口无监听，`dsa-web` / `dsa-schedule` 已 `disable --now` 并删除 unit。
日志轮转 `/etc/logrotate.d/dsa-web` → `/opt/dsa/logs/*.log`，weekly / rotate 4 / compress（名字沿用，实际覆盖全部 log）。

```bash
systemctl list-timers dsa-got.timer; systemctl is-enabled dsa-got.timer
systemctl show dsa-got.service -p MemoryMax -p ExecStart
```

### 入站与登录

| 项 | 值 |
|---|---|
| 监听 | 22 才有服务；8000 无监听（WebUI 已下线） |
| 入站隔离 | 采集时实测：22 从工作站可连（ssh 通）；8000 从公网不可达。**具体安全组规则未调 API 核对** |
| sshd | 仅公钥认证（`PasswordAuthentication no`、`PubkeyAuthentication yes`），运维以 root 登录 |
| 免密 | `/root/.ssh/authorized_keys` 1 把（工作站 `id_ed25519`，2026-10-24 补） |

```bash
ss -tlnp | grep -E ':22 |:8000'; sshd -T | grep -E '^(passwordauthentication|pubkeyauthentication|port) '
```

### 数据现状

- `/opt/dsa/code/data` → 软链 → `/opt/dsa/data`（`readlink -f` 应输出 `/opt/dsa/data`）。
- `stock_analysis.db` 20 MB / 41 张表。行数是**采集时点**（含 2026-10-24 对 `20261023` 的 1 次补跑，
  已越过迁移时原始行数）：`node4_raw_news` 14255、`node2_sector_trends` 12551、`node4_events` 1967、
  `catalyst_realizations` 807、`catalyst_events` 4、`catalyst_identities` 1、`holding_thesis_snapshots` 1、
  `trade_plans` 0（业务尚未产生计划行，非迁移问题）。
- `got_daily/` 61 个 8 位日期目录；非日期目录 `20260805_backup`、`20260817_pre_fix`（不进归档序列）；
  历史空壳日 `20260911`/`20260912`（仅空 `_replay/`）、`20261010`（仅 `candidate_pool.json`）——迁移前遗留。
- 归档完整性：605 个 JSON，唯一坏文件 `20260917/portfolio_decision.json`（工作站同款损坏，继承缺陷）。
- 已删除：`static/`（5.1 MB）、`apps/dsa-web/node_modules/`（305 MB）。

```bash
readlink -f /opt/dsa/code/data; du -sh /opt/dsa/data/stock_analysis.db
ls /opt/dsa/data/got_daily | grep -cE '^[0-9]{8}$'
```

### 出网可达性（2026-10-24 实测）

| 目标 | 结果 | 影响 |
|---|---|---|
| `github.com` / `raw.githubusercontent.com` | ✗ 超时/重置 | alphasift 装不上；litellm 价格表走本地 |
| `push2his` / `push2` / `1.push2his` / `82.push2`（东财行情） | ✗ TLS 起手后 RST，明文 80 同样 | efinance 实际不可用；GOT 路径未调用（日志 0 次） |
| `datacenter-web.eastmoney.com` | ✓ 200 | akshare 数据中心类接口可用 |
| `qt.gtimg.cn` / `web.ifzq.gtimg.cn` | ✓ 200 | 腾讯行情/K 线可用 |
| `hq.sinajs.cn` | 403（需 Referer） | 正常，代码已带 Referer |
| mootdx TCP 7709（5 个常用主站） | 2/5 可连（117.34.114.13、59.36.5.11） | 板块 K 线 212/212 成功 |
| `mirrors.aliyun.com` / `gitee.com` / `registry.npmmirror.com` | ✓ | 装包可用 |

### 推送目标

未配置（`has_notification=false`）。补哪个通道见上文「推送」表；填好后跑那条 `send(...)` 自测。

## 运维入口

- 日常：`workbench exec -i <实例ID> --command "..."`（当前唯一稳的入口）。
- ssh：需先往 `/root/.ssh/authorized_keys` 放公钥（默认空且密码登录关闭）；`ssh root@<ECS 公网IP>`。
- `workbench forward`（v1.0.4）返回 `port-forward is not available yet`，不可用；无 WebUI 后也不需要隧道。
