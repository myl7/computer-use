#!/bin/bash
# Build one full WebArena cell (7-stage protocol) on cs11369a.
#   usage: build_cell.sh <model> <tag> <max-cost-usd> [extra args]
# Run under nohup:  nohup bash build_cell.sh MODEL TAG CAP > logs/build-TAG.log 2>&1 &
set -u
cd ~/app/guiexp/webarena
source venv/bin/activate
set -a
source .env
set +a
export DOCKER_HOST=unix:///run/guiexp-docker.sock
export PYTHONUNBUFFERED=1

MODEL="$1"; TAG="$2"; CAP="$3"; shift 3 || true
mkdir -p logs experimental-results/guiexp_webarena
echo "=== build $MODEL ($TAG) cap \$$CAP $(date -Is) args=$* ==="
python3 -m guiexp_webarena.build_protocol \
  --family CommentPost \
  --model "$MODEL" \
  --out "experimental-results/guiexp_webarena/$TAG/CommentPost" \
  --max-cost-usd "$CAP" \
  --resume \
  "$@"
status=$?
echo "=== build $TAG exit $status $(date -Is) ==="
exit $status