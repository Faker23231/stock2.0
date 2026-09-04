#!/usr/bin/env bash
# 在 VPS 上以 root 执行：创建系统用户 + 安装 systemd 服务。
# 前提：代码已上传到 /opt/usstock（见 README），main.py 可执行
#   chmod +x main.py
# 用法:  sudo bash deploy/remote_setup.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/usstock}"
UNIT="usstockdesk"

if [ ! -f "$APP_DIR/main.py" ]; then
    echo "[错误] 找不到 $APP_DIR/main.py，请先上传代码(含 core/ web/ data/)。"
    exit 1
fi

echo "[1/4] 创建系统用户 usstock ..."
if ! id usstock >/dev/null 2>&1; then
    useradd -r -s /usr/sbin/nologin usstock
fi

echo "[2/4] 修正目录属主 ..."
mkdir -p "$APP_DIR"/{data,reports,logs}
chown -R usstock:usstock "$APP_DIR"

echo "[3/4] 安装 systemd 单元 ..."
# 把模板里的路径替换成实际路径再安装
sed "s#/opt/usstock#$APP_DIR#g" deploy/usstockdesk.service \
    > /etc/systemd/system/${UNIT}.service

echo "[4/4] 启用并启动 ..."
systemctl daemon-reload
systemctl enable --now "$UNIT" >/dev/null
sleep 2

if systemctl is-active --quiet "$UNIT"; then
    echo "[OK] ${UNIT} 已运行。"
    echo "     日志:  journalctl -u ${UNIT} -f"
    echo "     本机:  curl http://127.0.0.1:8756/api/ping"
    echo "     下一步: 配置 nginx + HTTPS（见 deploy/README.md 第 6 步）"
    echo "     提醒:   公网部署务必先在 data/config.json 配置 auth_token！"
else
    echo "[失败] 服务未启动，查看: journalctl -u ${UNIT} -n 50"
    exit 1
fi
