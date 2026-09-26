#!/usr/bin/env bash
set -euo pipefail

workspace=/home/yulong/app/guiexp/android
sdk=/home/yulong/app/guiexp/android-pair/third-party/android-sdk
state="$workspace/experimental-results/trace_verifier_20260925/orchestration"
pid_file="$state/android-w8-emulator.pid"
log_file="$state/android-w8-emulator.log"

if [[ -f "$pid_file" ]]; then
  old_pid=$(<"$pid_file")
  if [[ -r "/proc/$old_pid/cmdline" ]] && tr '\0' ' ' < "/proc/$old_pid/cmdline" | grep -q -- '-avd traceVerifier-w8'; then
    kill -TERM "$old_pid" 2>/dev/null || true
    for _ in $(seq 1 15); do
      kill -0 "$old_pid" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "$old_pid" 2>/dev/null || true
  fi
fi

nohup "$sdk/emulator/emulator" -avd traceVerifier-w8 -port 8644 -grpc 8646 \
  -no-window -no-audio -no-boot-anim -no-snapshot -no-metrics -wipe-data \
  > "$log_file" 2>&1 < /dev/null &
echo $! > "$pid_file"

for _ in $(seq 1 90); do
  if [[ "$("$sdk/platform-tools/adb" -s emulator-8644 shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; then
    PYTHONPATH="$workspace/computer-use" \
      "$workspace/../android-pair/.venv-android/bin/python" \
      -m guiexp_android.expa.setup_apps \
      --console-port 8644 --grpc-port 8646 --avd traceVerifier-w8
    exit 0
  fi
  sleep 2
done

echo "traceVerifier-w8 did not boot after clean recreation" >&2
exit 1
