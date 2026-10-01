#!/usr/bin/env bash
# 部署前檢查：硬體是否符合配置檔、必要工具是否齊全、twlegalrag 是否確實指向本機。
# 用法：./scripts/preflight.sh [profiles/edge3.env]
# 結束碼：有 FAIL 時為 1，只有 WARN 時為 0。
set -uo pipefail

PROFILE="${1:-$(dirname "$0")/../profiles/edge3.env}"
# shellcheck disable=SC1090
set -a; source "$PROFILE"; set +a

fails=0
ok()   { printf '  [OK]   %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; fails=$((fails + 1)); }

echo "== 硬體（$PROFILE）"
if command -v nvidia-smi >/dev/null 2>&1; then
  IFS=',' read -r gpu vram plimit < <(nvidia-smi --query-gpu=name,memory.total,power.limit \
    --format=csv,noheader,nounits | head -1)
  gpu="${gpu# }"; vram="${vram// /}"; plimit="${plimit// /}"
  if [ "${vram%.*}" -ge "$EDGE_MIN_VRAM_MIB" ]; then ok "GPU：$gpu，VRAM ${vram} MiB"
  else fail "GPU：$gpu，VRAM ${vram} MiB，低於 ${EDGE_MIN_VRAM_MIB} MiB"; fi
  if [ "${plimit%.*}" -gt "$EDGE_GPU_POWER_LIMIT_W" ]; then
    warn "GPU 功耗上限 ${plimit}W，建議：sudo nvidia-smi -pl ${EDGE_GPU_POWER_LIMIT_W}"
  else ok "GPU 功耗上限 ${plimit}W"; fi
  used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1 | tr -d ' ')
  [ "${used%.*}" -gt 1500 ] && warn "開機狀態已用 ${used} MiB VRAM，請確認螢幕接在內顯"
else
  fail "找不到 nvidia-smi（NVIDIA 驅動未安裝？）"
fi

ram_gb=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
if [ "$ram_gb" -ge "$EDGE_MIN_RAM_GB" ]; then ok "記憶體 ${ram_gb} GB"
else fail "記憶體 ${ram_gb} GB，低於 ${EDGE_MIN_RAM_GB} GB"; fi

swap_gb=$(awk '/SwapTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
[ "$swap_gb" -gt 16 ] && warn "swap ${swap_gb} GB；訓練時大量 swap 會加速 SSD 磨損，建議 8GB 以內"

for d in "$(dirname "$TLR_LOCAL_DB")" "$TLR_DOCS_ROOT"; do
  [ -d "$d" ] || { warn "資料目錄不存在：$d"; continue; }
  free_gb=$(df -BG --output=avail "$d" | tail -1 | tr -dc '0-9')
  if [ "$free_gb" -ge "$EDGE_MIN_FREE_DISK_GB" ]; then ok "$d 可用 ${free_gb} GB"
  else warn "$d 可用 ${free_gb} GB，低於 ${EDGE_MIN_FREE_DISK_GB} GB"; fi
done
[ -d "$TRAIN_CHECKPOINT_DIR" ] || warn "訓練 checkpoint 目錄不存在：$TRAIN_CHECKPOINT_DIR"

echo "== 軟體"
if python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then ok "Python $(python3 -V 2>&1 | cut -d' ' -f2)"
else fail "需要 Python 3.10 以上"; fi
python3 -c 'import pymupdf' 2>/dev/null || python3 -c 'import fitz' 2>/dev/null \
  && ok "PyMuPDF（讀 PDF）" || warn "未安裝 PyMuPDF，無法讀 PDF（pip install pymupdf）"
if command -v tesseract >/dev/null 2>&1; then
  if tesseract --list-langs 2>/dev/null | grep -qx chi_tra; then ok "tesseract＋繁中語言包（掃描檔 OCR）"
  else warn "tesseract 缺 chi_tra 語言包，掃描檔無法 OCR"; fi
else warn "未安裝 tesseract，掃描頁只會列入 needs_ocr"; fi
command -v soffice >/dev/null 2>&1 || warn "未安裝 LibreOffice，舊版 .doc 無法匯入"
if command -v llama-server >/dev/null 2>&1; then
  if llama-server --help 2>&1 | grep -q -- '--n-cpu-moe'; then ok "llama-server（支援 --n-cpu-moe）"
  else fail "llama-server 版本過舊，不支援 --n-cpu-moe"; fi
else warn "找不到 llama-server（本機 LLM）"; fi
command -v ollama >/dev/null 2>&1 && ok "ollama（DeepSafe 離線 AI）" || warn "找不到 ollama（DeepSafe 的離線 AI 透過 Ollama）"
if pgrep -x ollama >/dev/null 2>&1 && pgrep -x llama-server >/dev/null 2>&1; then
  fail "ollama 與 llama-server 同時執行，會各載一份模型；16GB VRAM / 64GB 記憶體放不下，請只留一個"
fi

echo "== 離線設定"
if command -v twlegalrag >/dev/null 2>&1; then
  cfg="${TWLEGALRAG_HOME:-$HOME/.twlegalrag}/config.toml"
  if grep -qE 'base_url *= *"http://(127\.0\.0\.1|localhost)' "$cfg" 2>/dev/null; then ok "twlegalrag 設定檔指向本機"
  else fail "twlegalrag 設定檔未指向本機，請執行 scripts/configure-twlegalrag.sh"; fi
  if [ -n "${TWLEGALRAG_TLR_BASE_URL:-}" ] && ! [[ "$TWLEGALRAG_TLR_BASE_URL" =~ ^http://(127\.0\.0\.1|localhost) ]]; then
    fail "環境變數 TWLEGALRAG_TLR_BASE_URL=$TWLEGALRAG_TLR_BASE_URL 會蓋過設定檔，查詢將送往外部"
  fi
  if twlegalrag health >/dev/null 2>&1; then ok "tlr_local 服務運作中"
  else warn "tlr_local 服務未啟動（python -m tlr_local serve）"; fi
else
  warn "未安裝 twlegalrag"
fi

echo
if [ "$fails" -gt 0 ]; then echo "結果：$fails 項 FAIL"; exit 1; fi
echo "結果：通過（請留意 WARN）"
