#!/usr/bin/env bash
# 把 twlegalrag 永久指向本機 tlr_local，避免漏設環境變數時查詢被送到公開端點。
set -euo pipefail

URL="${1:-http://127.0.0.1:8787}"
HOME_DIR="${TWLEGALRAG_HOME:-$HOME/.twlegalrag}"
mkdir -p "$HOME_DIR"
if [ -f "$HOME_DIR/config.toml" ]; then
  cp "$HOME_DIR/config.toml" "$HOME_DIR/config.toml.bak"
  echo "已備份原設定到 $HOME_DIR/config.toml.bak"
fi
cat > "$HOME_DIR/config.toml" <<EOF
[tlr]
base_url = "$URL"
EOF
echo "twlegalrag 已指向 $URL"
twlegalrag health || echo "提醒：tlr_local 服務尚未啟動（python -m tlr_local serve）"
