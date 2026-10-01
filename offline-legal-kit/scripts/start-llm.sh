#!/usr/bin/env bash
# 本機 LLM（llama.cpp llama-server），給 RTX 5070 Ti 16GB 與 DeepForge 共用一張卡。
#
# 做法：MoE 模型的 expert 權重放系統記憶體（30B Q4 約 18–19GB，Edge 3 的 64GB 放得下），
# GPU 只放注意力層與 KV cache，大約佔 4–6GB VRAM，其餘留給 DeepForge 訓練。
# 搭配配置檔：set -a; source profiles/edge3.env; set +a
#
# 用法：MODEL=/models/xxx.gguf ./scripts/start-llm.sh
# 可調參數（環境變數）：
#   CPU_MOE   放到 CPU 的 MoE 層數，越大越省 VRAM、越慢（預設 999 = 全部）
#   CTX       context 長度（預設 32768，書狀與判決需要長 context）
#   PORT      預設 8080，只綁 127.0.0.1
set -euo pipefail

: "${MODEL:?請設定 MODEL=/path/to/model.gguf（建議 30B 級 MoE、Q4_K_M 量化）}"
CPU_MOE="${CPU_MOE:-999}"
CTX="${CTX:-32768}"
PORT="${PORT:-8080}"
THREADS="${THREADS:-$(nproc)}"

exec llama-server \
  --model "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  --n-gpu-layers 999 \
  --n-cpu-moe "$CPU_MOE" \
  --ctx-size "$CTX" \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --flash-attn on \
  --threads "$THREADS" \
  --jinja \
  --alias local-legal
