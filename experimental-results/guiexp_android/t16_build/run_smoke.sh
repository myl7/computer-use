#!/bin/bash
# Smoke build: one cell, ContactsAddContact x GLM, cap USD 0.50.
set -u
cd /Users/myl/app/computer-use/computer-use || exit 1
ENV_FILE=/Users/myl/app/.env
[ -f /Users/myl/app/computer-use/.env ] && ENV_FILE=/Users/myl/app/computer-use/.env
set -a; . "$ENV_FILE"; set +a
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY missing from $ENV_FILE}"
: "${OPENROUTER_BASE_URL:?OPENROUTER_BASE_URL missing from $ENV_FILE}"
PY=../.venv-android/bin/python
echo "=== smoke start $(date '+%Y-%m-%dT%H:%M:%S')"
$PY -m guiexp_android.build_protocol \
  --family ContactsAddContact --model z-ai/glm-5.3-flash \
  --seeds 1,2,3 --k 1,2,3 --deploy-uses 30 --doc-seeds 4,5,6 \
  --max-cost-usd 0.50 \
  --out ../experimental-results/guiexp_android/t16_build/z-ai_glm-5.3-flash/ContactsAddContact \
  --keep-emulator
echo "=== smoke finished rc=$? $(date '+%Y-%m-%dT%H:%M:%S')"
