#!/bin/bash
# Pilot: one build-protocol cell, model qwen/qwen3.8-flash, family ContactsAddContact.
set -u
cd /Users/myl/app/computer-use/code || exit 1
set -a; . /Users/myl/app/.env; set +a
: "${OPENROUTER_API_KEY:?missing}"
: "${OPENROUTER_BASE_URL:?missing}"
exec ../.venv-android/bin/python -m guiexp_android.build_protocol \
  --family ContactsAddContact --model qwen/qwen3.8-flash \
  --seeds 1,2,3 --k 1,2,3 --deploy-uses 30 --doc-seeds 4,5,6 \
  --max-cost-usd 1.20 \
  --out ../experimental-results/guiexp_android/qwen_pilot/qwen_qwen3.8-flash/ContactsAddContact \
  --keep-emulator
