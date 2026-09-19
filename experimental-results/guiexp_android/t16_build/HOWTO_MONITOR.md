# Monitoring the t16 build batch

The batch is one detached process running `run_batch.sh`, which walks the
fourteen cells of `build_protocol.BATCH_FAMILIES` x `BATCH_MODELS`
sequentially on the one AVD. Cheapest step cap first, GLM before DeepSeek, so
a budget stop loses the least work.

Files in this directory:

| file | what it holds |
|---|---|
| `batch.pid` | the batch process id |
| `batch.log` | everything the batch and every cell printed |
| `progress.json` | rewritten after every cell: cells done, cumulative spend, last cell and its status |
| `emulator.pid` | the emulator this session booted |
| `run_batch.sh` | the batch itself |
| `<model_slug>/<Family>/build.json` | one finished cell's record |

## Is it still running

```bash
ps -p "$(cat experimental-results/guiexp_android/t16_build/batch.pid)" \
  && echo RUNNING || echo NOT RUNNING
```

## How far has it got

```bash
cat experimental-results/guiexp_android/t16_build/progress.json
```

`cells_done` out of `cells_total`, `spend_usd` against `overall_cap_usd`, and
`last_cell` with `last_status` (`running`, `ok`, `failed_rc<N>`,
`stopped_over_budget`, `finished`).

## Watch the log

```bash
tail -f experimental-results/guiexp_android/t16_build/batch.log

# just the cell boundaries and their costs
grep -E "^=== " experimental-results/guiexp_android/t16_build/batch.log

# anything that went wrong
grep -nE "Traceback|Error|BudgetExceeded|failed_rc" \
  experimental-results/guiexp_android/t16_build/batch.log
```

## Which cells are finished

```bash
ls -d experimental-results/guiexp_android/t16_build/*/*/ 2>/dev/null
find experimental-results/guiexp_android/t16_build -name build.json | sort
```

## Spend so far

`progress.json`'s `spend_usd` is the running total the batch itself enforces.
To recount it from the records:

```bash
find experimental-results/guiexp_android/t16_build -name build.json \
  -exec .venv-android/bin/python -c '
import json,sys
d=json.load(open(sys.argv[1]))
print(sys.argv[1], d.get("total_cost_usd"))' {} \;
```

The batch stops itself when the cumulative spend reaches USD 5.00, and each
cell carries `--max-cost-usd 0.60`.

## Stop it cleanly

Kill the batch script, which stops it between cells and leaves every finished
cell's `build.json` intact. The cell that is running at that moment is lost.

```bash
kill "$(cat experimental-results/guiexp_android/t16_build/batch.pid)"
```

If a `build_protocol` child survives the parent, stop that too, by its own pid:

```bash
pgrep -f "guiexp_android.build_protocol"      # read the pid first
kill <that pid>
```

Do not `pkill -f python`: other work on this machine uses the same interpreter.

## The emulator

The batch passes `--keep-emulator` on every cell, so it never shuts the AVD
down. Stop it only when the batch is finished:

```bash
third-party/android-sdk/platform-tools/adb emu kill
# or, by the pid this session recorded
kill "$(cat experimental-results/guiexp_android/t16_build/emulator.pid)"
```

## Resuming

`run_batch.sh` has no resume flag. A cell whose output directory already holds
a `build.json` can be skipped by editing the `FAMILIES` and `MODELS` lists at
the top of the script to the cells still owed, then launching it again the
same way:

```bash
cd /Users/myl/app/computer-use
nohup bash experimental-results/guiexp_android/t16_build/run_batch.sh \
  > experimental-results/guiexp_android/t16_build/batch.log 2>&1 &
echo $! > experimental-results/guiexp_android/t16_build/batch.pid
```
