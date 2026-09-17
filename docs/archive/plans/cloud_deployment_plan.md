# DSA 上云部署计划
> **原位置**：`docs/custom/cloud_deployment_plan.md`（2026-09-17 文档结构重排移入当前位置）

> 状态：**挂起待确认**（截至 2026-09-14 盘点里程碑全部待执行，见 `docs/got/archive/got_status_0914.md`；如不再上云可删除本页）

> 目标：将 Daily Stock Analysis 部署到阿里云轻量应用服务器，每日定时运行 GOT 分析 + 选股，报告自动推送到企业微信。

---

## 一、服务器配置

### 选型：轻量应用服务器 2核2G

| 配置 | 规格 | 评估 |
|------|------|------|
| CPU | 2核 | ✅ IO密集型，LLM调用走API，CPU空闲率高 |
| 内存 | 2GB | ⚠️ 峰值 ~1.2GB，需 swap 兜底 |
| 带宽 | 200M 峰值 | ✅ 单次运行传输 ~200MB，足够 |
| 磁盘 | 40GB ESSD | ✅ 当前 data/ 仅 11MB，可存 5-10 年 |
| 系统盘 | 默认 | ✅ Python 环境 + 依赖 ~3GB |

### 镜像：Ubuntu 24.04 LTS

选择理由：
- 预装 Python 3.12，与本地开发环境一致
- LTS 支持到 2029 年
- 社区资源最丰富，踩坑好查
- apt 包管理方便

**不选 CentOS**：已停止维护，安全风险。

### 月费预估

| 方案 | 配置 | 月费 | 适用场景 |
|------|------|------|---------|
| 当前 | 2核2G | ~50元 | 够用，需 swap 兜底 |
| 推荐 | 2核4G | ~80元 | 内存翻倍，彻底消除 OOM |
| 高性能 | 4核8G | ~150元 | 未来扩展（多策略/回测） |

---

## 二、部署流程

### 2.1 系统初始化

```bash
# SSH 登录服务器后执行

# 1. 系统更新 + 基础依赖
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git build-essential

# 2. 创建 2GB swap（防止 OOM）
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 3. 时区设置（确保 cron 按北京时间执行）
sudo timedatectl set-timezone Asia/Shanghai
```

### 2.2 项目部署

```bash
# 4. 克隆项目
cd /opt
sudo git clone <your-repo-url> daily_stock_analysis
sudo chown -R $USER:$USER daily_stock_analysis
cd daily_stock_analysis

# 5. 虚拟环境（精简依赖，不装 PaddleOCR）
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# 精简安装（跳过 PaddleOCR，节省 1-2GB）
pip install -r requirements.txt
pip uninstall -y paddlepaddle paddleocr

# 6. 环境变量
cp .env.example .env  # 或手动创建
nano .env
```

### 2.3 环境变量配置

```bash
# ===== LLM =====
LITELLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=sk-xxx

# ===== 数据源 =====
ENABLE_EASTMONEY_PATCH=true

# ===== 通知（企业微信）=====
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY
WECHAT_MSG_TYPE=text

# ===== 通知（邮件，可选）=====
EMAIL_SENDER=dsa-bot@qq.com
EMAIL_PASSWORD=your-auth-code
EMAIL_RECEIVERS=your-email@example.com
```

### 2.4 定时任务

```bash
# crontab -e
# 交易日 15:30 运行（收盘后 30 分钟，数据已更新）
# main.py 内置交易日检查，非交易日自动跳过

30 15 * * 1-5 cd /opt/daily_stock_analysis && \
    /opt/daily_stock_analysis/venv/bin/python main.py --got \
    >> /var/log/dsa.log 2>&1
```

### 2.5 验证

```bash
# 1. Python 版本
python3 --version  # 应为 3.12.x

# 2. 环境检查
source venv/bin/activate
python scripts/check_env.py

# 3. 网络连通（mootdx TCP 7709）
nc -zv mqtt.195177.com 7709

# 4. 数据库初始化
python -c "from src.storage import DatabaseManager; DatabaseManager().init_db()"

# 5. 测试运行
python main.py --got

# 6. 测试企业微信推送
python -c "
from src.config import Config
from src.notification_sender.wechat_sender import WechatSender
sender = WechatSender(Config())
sender.send_to_wechat('DSA 部署测试成功')
"
```

---

## 三、报告推送方案

### 3.1 企业微信群机器人（推荐，零成本）

**创建步骤**：
1. 企业微信群 → 右上角 "..." → 群机器人 → 添加
2. 复制 Webhook URL 填入 `.env`

**推送效果**：每日 15:30 自动推送完整分析报告到企业微信群

**限制**：
- 单向推送（只能发，不能收）
- Markdown 4096 字节 / Text 2048 字节（超长自动分片）
- 图片 ≤2MB

### 3.2 多渠道组合（可选）

| 渠道 | 用途 | 配置 |
|------|------|------|
| 企业微信 | 实时查看（手机/PC） | `WECHAT_WEBHOOK_URL` |
| 邮件 | 归档留存 | `EMAIL_SENDER` + `EMAIL_PASSWORD` |
| 飞书 | 双向交互（未来扩展） | `FEISHU_APP_ID` + `FEISHU_APP_SECRET` |

---

## 四、持仓更新方案

### 4.1 现状

| 能力 | 状态 | 说明 |
|------|------|------|
| OCR 持仓识别 | ✅ 已实现 | `PortfolioOcrImportService` + PaddleOCR |
| 截图导入脚本 | ✅ 已实现 | `scripts/import_holdings_from_screenshot.py` |
| 企业微信推送 | ✅ 已实现 | 单向，只能发报告 |
| 企业微信双向交互 | ⚠️ 框架有，实现缺 | `handle_wecom_webhook` 存在，platform 未实现 |
| 飞书/钉钉双向 bot | ✅ 已实现 | 支持接收消息 + 图片 |

### 4.2 方案对比

| 方案 | 开发量 | 易用性 | 说明 |
|------|--------|--------|------|
| **A. 命令行脚本** | 0 天 | ⭐⭐ | 现在就能用，scp 传图 + OCR |
| **B. 飞书 bot** | 0.5 天 | ⭐⭐⭐⭐ | 已有双向框架，加个命令 |
| **C. 企业微信应用** | 2-3 天 | ⭐⭐⭐⭐⭐ | 需开发 wecom platform |
| **D. Web UI 上传** | 0 天 | ⭐⭐⭐ | FastAPI 已有，需加页面 |

### 4.3 推荐路径：先 A 后 B

**Phase 1（立即）**：命令行脚本
```bash
# 手机截图 → scp 传到服务器 → OCR 自动识别
scp screenshot.jpg root@server:/opt/daily_stock_analysis/tmp/
ssh root@server "cd /opt/daily_stock_analysis && \
    source venv/bin/activate && \
    python scripts/import_holdings_from_screenshot.py \
    --image tmp/screenshot.jpg --account 1 --sync"
```

**Phase 2（稳定运行后）**：飞书/钉钉 bot
```
手机截图 → 飞书发送给 bot → 自动 OCR → 回复识别结果 → 确认写入
```

**Phase 3（按需）**：企业微信自建应用
```
需要：注册企业微信应用 → 配置回调 URL → 实现 bot/platforms/wecom.py
开发量：2-3 天
```

---

## 五、东财反爬应对

服务器 IP 与本地 IP 不同，东财反爬行为可能不同。

### 已集成的防护

```bash
# .env 中启用
ENABLE_EASTMONEY_PATCH=true
```

防护措施：
- NID 授权令牌（`eastmoney_patch.py`）
- 随机 User-Agent
- 请求间隔 + 随机抖动（`em_rate_limiter.py`）
- mootdx TCP 优先（不受 HTTP 反爬影响）

### 如果仍被封

1. 检查 mootdx TCP 是否正常（TCP 协议不受 HTTP 反爬影响）
2. 配置代理（`USE_PROXY=true`）
3. 调整请求间隔（`EM_RATE_LIMIT_SECONDS=2`）

---

## 六、运维监控

### 6.1 日志管理

```bash
# 查看实时日志
tail -f /var/log/dsa.log

# 日志轮转（防止占满磁盘）
sudo tee /etc/logrotate.d/dsa << 'EOF'
/var/log/dsa.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
}
EOF
```

### 6.2 健康检查

```bash
# 检查运行状态
systemctl status cron
grep -c "GOT.*开始执行" /var/log/dsa.log  # 本月运行次数
grep "ERROR\|FAILED" /var/log/dsa.log | tail -5  # 最近错误

# 检查磁盘空间
df -h /
du -sh /opt/daily_stock_analysis/data/
```

### 6.3 失败告警

```bash
# 简易方案：cron 脚本检测连续失败，发企业微信告警
# 每 30 分钟检查一次
*/30 * * * 1-5 \
    last_run=$(tail -100 /var/log/dsa.log | grep -c "$(date +%Y-%m-%d).*GOT.*完成") && \
    today_runs=$(grep -c "$(date +%Y-%m-%d).*GOT.*完成" /var/log/dsa.log) && \
    if [ "$today_runs" -eq 0 ] && [ "$(date +%H)" -ge 16 ]; then \
        curl -s "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY" \
            -H 'Content-Type: application/json' \
            -d '{"msgtype":"text","text":{"content":"⚠️ DSA 今日未成功运行，请检查日志"}}'; \
    fi
```

---

## 七、里程碑

| 阶段 | 任务 | 预计耗时 | 状态 |
|------|------|---------|------|
| **M1** | 购买服务器 + 系统初始化 | 0.5 天 | 待执行 |
| **M2** | 项目部署 + 依赖安装 + .env 配置 | 0.5 天 | 待执行 |
| **M3** | 首次端到端运行验证（GOT + Phase 2） | 0.5 天 | 待执行 |
| **M4** | 企业微信推送配置 + 测试 | 0.5 天 | 待执行 |
| **M5** | cron 定时任务 + 日志轮转 + 告警 | 0.5 天 | 待执行 |
| **M6** | 观察 3 天，调优内存/反爬/LLM | 1 天 | 待执行 |
| **总计** | | **~3.5 天** | |

---

## 八、成本汇总

| 项目 | 月费 | 说明 |
|------|------|------|
| 阿里云轻量服务器 | ~50 元 | 2核2G / Ubuntu 24.04 |
| DeepSeek API | ~10-30 元 | 每日 3-5 次 LLM 调用 |
| 企业微信机器人 | 0 元 | 免费 |
| LLM 搜索（可选） | 0-50 元 | Bocha/Tavily 免费版 |
| **合计** | **~60-130 元/月** | |
