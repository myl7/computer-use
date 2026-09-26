"""Free local provider-readiness polling for a stopped experiment.

No credentials or model requests. Launches the prepared guarded runner once
when its unchanged provider bounds validate and the host is awake.
"""
from __future__ import annotations
import argparse,fcntl,hashlib,importlib,json,os,subprocess,time
from pathlib import Path
from .budget_client import BudgetStop,atomic_json
from .event_watchdog import dispatch
from .matched_run import DEFAULT_OUT,ROOT


def host_awake():
    r=subprocess.run(['ioreg','-r','-k','AppleClamshellState','-d','4'],capture_output=True,text=True,timeout=10)
    return r.returncode==0 and '"AppleClamshellState" = No' in r.stdout


def run(version,interval=60,timeout=10800):
    out=DEFAULT_OUT;path=out/'provider_wait.json';started=time.time();deadline=time.monotonic()+timeout
    module=importlib.import_module(f'guiexp_android.matched_run_v{version}')
    spec=module.load_spec(out)
    with (out/'provider_wait.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('A provider readiness waiter already exists.')
        state={'pid':os.getpid(),'version':version,'started_unix':started,'model_calls':0,'interval_seconds':interval}
        while True:
            state['updated_unix']=time.time()
            if not host_awake():
                state['status']='waiting_for_host_wake';atomic_json(path,state)
                deadline=time.monotonic()+timeout;time.sleep(interval);continue
            try:
                for model,bounds in spec['model_locks'].items():module.validated_metadata(model,bounds)
            except BudgetStop as exc:
                reason=str(exc)
                transient=('not active' in reason or 'unavailable after 3 free GET' in reason or 'unavailable or ambiguous' in reason)
                state.update(status='waiting_for_provider',reason=reason);atomic_json(path,state)
                if not transient or time.monotonic()>=deadline:
                    state['status']='provider_wait_needs_attention';atomic_json(path,state)
                    event={'key':hashlib.sha256(f'provider-wait:{started}'.encode()).hexdigest()[:24],'reason':'provider_readiness_unresolved','detected_unix':time.time(),'snapshot':state}
                    dispatch(event,out/'event_watchdog');return
                time.sleep(interval);continue
            with (out/'run.lock').open('a') as f:
                try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    state['status']='another_runner_already_active';atomic_json(path,state);return
            with (out/'batch.log').open('ab',buffering=0) as log:
                cmd=['../.venv-android/bin/python','-m','guiexp_android.supervise_matched_revision','--version',str(version)]
                proc=subprocess.Popen(cmd,cwd=ROOT/'computer-use',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            state.update(status='launched_guarded_runner',supervisor_pid=proc.pid);atomic_json(path,state)
            monitor=out/'monitor_state.json'
            if monitor.exists():
                m=json.loads(monitor.read_text());m.update(updated_unix=time.time(),status='running',run_version=version,supervisor_pid=proc.pid,active_repair_agent=None,current_action='Provider recovered; local non-model readiness waiter launched the prepared runner once.');atomic_json(monitor,m)
            return


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--version',type=int,required=True);args=ap.parse_args()
    try:run(args.version)
    except Exception as exc:
        event={'key':hashlib.sha256(f'provider-wait-process:{os.getpid()}'.encode()).hexdigest()[:24],'reason':'local_provider_waiter_failed','detected_unix':time.time(),'error_type':type(exc).__name__}
        dispatch(event,DEFAULT_OUT/'event_watchdog')
        raise

if __name__=='__main__':main()
