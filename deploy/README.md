# 部署到云服务器（VPS）指南

把「美股持仓管理平台」作为**自托管服务**跑在一台 Linux VPS 上，iPhone /
任意浏览器通过 HTTPS 访问（PWA 可直接"加到主屏幕"当 App 用）。

> 后端仍是 Python（**纯标准库，无需 pip 依赖**），本目录给的是 Linux 部署配套，
> 与 Windows exe 互不影响：exe 在本地电脑用，本方案在服务器用源码直接跑。

---

## 1. 架构

```
iPhone / 浏览器
   │  HTTPS（nginx 终止 TLS，PWA 必需）
   ▼
nginx :443 ──反向代理──▶ python3 main.py :8756 (只监听 127.0.0.1)
                              ├─ data/   持仓/配置/快照(JSON)
                              ├─ reports/ 日报 HTML
                              └─ logs/   日志
```

- 行情/快讯上游：腾讯财经、东方财富、华尔街见闻。**推荐中国大陆的 VPS**
  （这些源对境外/海外机房 IP 可能限流或不可达）。
- 项目**纯 Python 标准库**，服务器只需 python3（≥3.9 均可），无需 venv/pip。

---

## 2. 服务器准备

```bash
# Debian/Ubuntu 示例
sudo apt update && sudo apt install -y python3 nginx
python3 -V   # ≥ 3.9
```

## 3. 上传代码与数据

把以下内容传到服务器（例如 `/opt/usstock`）：

```
/opt/usstock/
├── main.py
├── core/            # 整个目录
├── web/             # 整个目录（含 manifest/sw.js/图标）
├── data/            # 你的真实数据（持仓/配置/快照）—— 迁移核心！
└── reports/         # 可空，历史日报
```

本地数据迁移（建议先停本地 exe，再复制 `data/` 与 `reports/`）：

```bash
# 在本地 Windows（git bash）执行
scp -r data/*  user@你的VPS:/opt/usstock/data/
scp -r reports/* user@你的VPS:/opt/usstock/reports/
```

> `data/config.json` 里有邮件授权码等敏感信息，上传后执行：
> `sudo chmod 700 /opt/usstock/data /opt/usstock/reports`（配合下面专用系统用户）。

## 4. 配置访问口令（公网必做！）

默认**无鉴权**——任何人都能看你的持仓、改数据。公网部署务必开启：

编辑 `data/config.json`，加入顶层字段：

```json
{
  "auth_token": "换成一段长随机字符串",
  ...原有配置保持不变...
}
```

生成随机口令：`python3 -c "import secrets;print(secrets.token_urlsafe(24))"`

开启后：
- 所有 `/api/*`（`/api/ping` 除外）与 `/reports/*` 需要 `X-Auth-Token` 请求头
  或 `?token=` 查询参数；
- 前端检测到 401 会自动弹出「访问口令」输入框，输入一次保存在本机
  localStorage，之后自动带上；
- 关闭鉴权：把该字段删掉（或留空）即可。局域网自用可不开。

## 5. 注册为系统服务（systemd）

创建专用用户与目录后，使用本目录模板：

```bash
sudo useradd -r -s /usr/sbin/nologin usstock   # 已存在则跳过
sudo mkdir -p /opt/usstock && sudo chown -R usstock:usstock /opt/usstock
sudo cp deploy/usstockdesk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now usstockdesk
sudo systemctl status usstockdesk            # active (running)
journalctl -u usstockdesk -f                  # 看日志
```

模板里的定时任务（每日日报 07:30 / 盘前 20:30，服务器本地时区）默认开启；
把服务器时区设成你常用时区：

```bash
sudo timedatectl set-timezone Asia/Shanghai
```

## 6. HTTPS（PWA 必需）

Service Worker / PWA 只在 HTTPS（或 localhost）下生效，公网必须配证书：

```bash
sudo cp deploy/nginx.conf /etc/nginx/conf.d/usstock.conf
# 编辑 /etc/nginx/conf.d/usstock.conf，把 server_name 改成你的域名
sudo nginx -t && sudo systemctl reload nginx

# 域名解析到本机后签发证书（免费）
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名
```

没有域名也可以：用 Cloudflare Tunnel / frp / tailscale 之类暴露，或
`certbot --nginx` 配合 IP 需自有证书（一般建议直接买个域名，几块钱一年）。

## 7. 防火墙

```bash
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw enable
```

## 8. iPhone 使用（PWA）

1. Safari 打开 `https://你的域名/`；
2. 首次会弹「访问口令」，输入 `auth_token`；
3. 点分享按钮 → **「添加到主屏幕」**；
4. 主屏幕出现带图标的"美股持仓"入口，全屏独立运行（standalone），
   下拉刷新与轮询照常；下次打开自动带缓存口令。

> 仅同局域网（不走公网）临时体验也可以：本地 `python main.py --host 0.0.0.0`
> + 手机连同一 WiFi + 放行 Windows 防火墙 8756 端口，浏览器访问
> `http://电脑IP:8756/`。此场景无 HTTPS，Service Worker 不启用，仍是普通网页。

---

## 常见问题

| 现象 | 处理 |
|---|---|
| 信号/快讯拉不到 | VPS 在中国大陆外或 IP 被限流，换大陆机房，或放弃云、走局域网/内网穿透 |
| 日报时间是"服务器时间" | `timedatectl set-timezone` 设成你所在时区；美股开收盘按美东自然日，跨时区以你配置的 HH:MM 触发 |
| 想发邮件推送 | 设置页填 SMTP（QQ 邮箱用授权码），测试发送；服务器出站 465 需放行 |
| 数据多端同步 | 权威数据在服务器 `data/`。本地 exe 与云实例不要同时改同一份持仓，以免互相覆盖 |
| 如何升级 | 停服务 → 覆盖 main.py/core/web → 起服务；`data/` 不要覆盖 |
| auth_token 忘了 | 服务器上直接编辑 data/config.json 改掉即可，前端下次 401 会重新弹输入框 |

## 9. 快捷脚本

`deploy/remote_setup.sh` 封装了第 5 步（系统用户 + systemd），在服务器上以
root 执行；nginx/certbot 步骤建议按上面文档手动做（涉及域名与证书交互）。
