"""Run one guarded batch and write terminal status plus incremental analysis.

This supervisor does not retry the batch or touch credentials. Transport
retry and the shared spending ceiling belong to the selected frozen client.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .budget_client import atomic_json
from .matched_run import DEFAULT_OUT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',type=int,default=5)
    args=parser.parse_args()
    out=DEFAULT_OUT
    version=args.version
    if not (out/f'spec.v{version}.json').exists() or not Path(__file__).with_name(f'matched_run_v{version}.py').exists():
        raise SystemExit('Requested frozen run version is unavailable.')
    progress_path=out/f'progress.v{version}.json'
    state=dict(pid=os.getpid(),started_unix=time.time(),status='running',version=version,
               command=['../.venv-android/bin/python','-m',f'guiexp_android.matched_run_v{version}','--run'])
    atomic_json(out/'batch_status.json',state)
    try:
        process=subprocess.Popen(state['command'],cwd=Path(__file__).resolve().parents[1])
        state['worker_pid']=process.pid
        atomic_json(out/'batch_status.json',state)
        last_analysis=0.0
        while True:
            try:
                code=process.wait(timeout=60)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic()-last_analysis>=120:
                    subprocess.run([sys.executable,'-m','guiexp_android.matched_analyze'],check=False)
                    last_analysis=time.monotonic()
                progress=progress_path
                if progress.exists():
                    current=json.loads(progress.read_text())
                    state.update(counts=current['counts'],complete_pairs=current['complete_pairs'],budget=current['budget'])
                    atomic_json(out/'batch_status.json',state)
        state['runner_exit_code']=code
        analysis=subprocess.run([sys.executable,'-m','guiexp_android.matched_analyze'],check=False)
        state['analysis_exit_code']=analysis.returncode
        progress=progress_path
        if progress.exists():
            result=json.loads(progress.read_text())
            state['counts']=result['counts']
            state['complete_pairs']=result['complete_pairs']
            state['budget']=result['budget']
            state['status']='completed' if not any(result['counts'].get(k,0) for k in ('pending','running','budget_stopped','interrupted')) else 'stopped_with_partial_results'
        else:
            state['status']='stopped_before_progress'
    except BaseException as exc:
        state['status']='supervisor_interrupted'
        state['error_type']=type(exc).__name__
        raise
    finally:
        state['ended_unix']=time.time()
        atomic_json(out/'batch_status.json',state)
    return code


if __name__=='__main__':raise SystemExit(main())
