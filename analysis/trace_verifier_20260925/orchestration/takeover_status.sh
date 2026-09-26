#!/usr/bin/env bash
set -u

repo=/Users/myl/app/computer-use
snapshot="$repo/analysis/trace_verifier_20260925/orchestration/remote-snapshots"
mkdir -p "$snapshot/cs659b" "$snapshot/cs11369a"

echo "== cs659b live jobs =="
ssh cs659b 'pgrep -af "run_cell.py|supervise_android_queue|traceVerifier-w|recover_android_w8" | head -80; free -h | head -2; df -h /home | tail -1'
echo "== cs11369a live jobs =="
ssh cs11369a 'pgrep -af "run_three_building.py|run_desktop_qwen_chain" | head -60; DOCKER_HOST=unix:///run/guiexp-docker.sock docker ps --format "{{.Names}} {{.Status}}" | grep guiexp-osworld || true; free -h | head -2'

rsync -a cs659b:/home/yulong/app/guiexp/android/experimental-results/trace_verifier_20260925/ "$snapshot/cs659b/"
rsync -a cs11369a:/home/myl/app/guiexp/osworld/experimental-results/trace_verifier_20260925/ "$snapshot/cs11369a/desktop/"
rsync -a cs11369a:/home/myl/app/guiexp/webarena/experimental-results/trace_verifier_20260925/ "$snapshot/cs11369a/web/"

cd "$repo" || exit 1
python3 analysis/trace_verifier_20260925/results/validate_results.py
