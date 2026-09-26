"""Local, model-free polling; queue this Codex task only on actionable events.

No API credentials, model calls, scheduled messages, UI actions, or retries of
experiments occur here. Recovery is delegated once per incident to the existing
task through the documented `codex queue` CLI.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'experimental-results/guiexp_android/revision_20260913'
THREAD = '01a0955d-6a11-7a90-a5f6-857c346d5413'
CODEX = '/opt/homebrew/bin/codex'
STALE_SECONDS = 900


def read_json(path, default=None):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return default


def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w') as f:
        json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def alive(pid):
    if not isinstance(pid,int) or pid<=0:return False
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


def snapshot(out=OUT):
    b=read_json(out/'batch_status.json',{})
    m=read_json(out/'monitor_state.json',{})
    with sqlite3.connect(f'file:{out / "budget.sqlite3"}?mode=ro',uri=True,timeout=5) as db:
        db.execute('BEGIN')
        latest=db.execute('SELECT id,state,created FROM calls ORDER BY created DESC LIMIT 1').fetchone()
        total=db.execute("SELECT COALESCE(SUM(actual_nano),0),COALESCE(SUM(CASE WHEN state!='settled' THEN reserved_nano ELSE 0 END),0),SUM(CASE WHEN state='overrun' THEN 1 ELSE 0 END) FROM calls").fetchone()
    lock_held=False
    with (out/'run.lock').open('a') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(f,fcntl.LOCK_UN)
        except BlockingIOError:lock_held=True
    version=b.get('version',7)
    progress=read_json(out/f'progress.v{version}.json',{})
    # State files describe actual episode starts/completions, while a progress
    # summary can lag for the full duration of a long episode.
    active=[]
    ep_root=out/'episodes'
    for path in ep_root.rglob('state.json'):
        item=read_json(path,{})
        if item.get('status')=='running':active.append(item.get('started_unix',path.stat().st_mtime))
    return dict(now=time.time(),batch=b,counts=progress.get('counts',b.get('counts',{})),
                supervisor_alive=alive(b.get('pid')),worker_alive=alive(b.get('worker_pid')),
                run_lock_held=lock_held,latest_request=latest,
                active_episode_started=max(active,default=0),
                actual_usd=total[0]/1e9,reserved_usd=total[1]/1e9,
                occupied_usd=(total[0]+total[1])/1e9,overrun=bool(total[2]),
                repair_in_progress=bool(m.get('active_repair_agent')))


def event_for(s):
    b=s['batch'];counts=s['counts']
    if not b:return None
    identity=f"{b.get('pid')}:{b.get('started_unix')}"
    latest=s.get('latest_request')
    progress_token=latest[0] if latest else 'no-request'
    active=s['worker_alive'] or s['run_lock_held']
    reason=None
    if s['overrun'] or s['occupied_usd']>10.000000001:
        reason='budget_overrun'
    elif not active and not s['supervisor_alive']:
        if counts and counts.get('pending',0)==0 and counts.get('running',0)==0:
            reason='finished_with_recorded_outcomes'
        else:reason='batch_stopped'
    elif not s['repair_in_progress']:
        last_progress=max(b.get('started_unix',0),s.get('active_episode_started',0),latest[2] if latest else 0)
        if s['now']-last_progress>STALE_SECONDS:reason='no_progress_15_minutes'
    if not reason or (s['repair_in_progress'] and reason!='budget_overrun'):return None
    # A stalled run subsequently exiting with the same last request is one
    # incident, not two notifications. New progress creates a new identity.
    key=hashlib.sha256(f'{identity}|{progress_token}'.encode()).hexdigest()[:24]
    return dict(key=key,reason=reason,detected_unix=s['now'],snapshot=s)


def prompt_for(event, path):
    return (
        f"本地事件守护进程检测到 GUI 实验事件：{event['reason']}。详情在 {path}。"
        "这是异常触发的一次请求，不是定时唤醒。先检查最新 batch/progress、真实PID、run.lock、最近receipt和宿主机是否正常唤醒。"
        "在 /Users/myl/app/computer-use 工作，读取实验记录后处理根因；只允许一个runner，保留冻结spec、原始结果和未知费用，不能重放已完成UI或盲目重发旧请求。"
        "所有版本共用 experimental-results/guiexp_android/revision_20260913/budget.sqlite3，实际费用加全部未决/进行中预留及新预留不得超过US$10；预计达到或超过100港元须用户许可。"
        "保持论文正文/numbers不动。复用最新受预算保护的版本和冻结相对Python调用；必要修复先测试和版本化。"
        "在休眠/DarkWake期间不启动付费任务，不更改系统休眠设置。"
        "可恢复故障在现有预算内处理，无法安全恢复或多次同类故障无有效数据时停止付费并简明报告。"
        "若全部可执行工作已结束，运行matched_analyze汇报结果与限制。不要创建或恢复scheduled message/heartbeat。"
        "处理完成后更新monitor_state.json清除active_repair_agent；本地watchdog会继续无模型轮询。"
    )


def dispatch(event, folder, sender=subprocess.run):
    path=folder/'events'/f"{event['key']}.json"
    if path.exists():return False
    # Persist before delivery. A CLI timeout might already have queued the
    # event, so never automatically repeat an uncertain delivery.
    record=dict(event,delivery='dispatching')
    write_json(path,record)
    try:
        result=sender([CODEX,'queue','--thread',THREAD,'--message',prompt_for(event,path)],
                      cwd=ROOT,capture_output=True,text=True,timeout=60)
        record['delivery']='queued' if result.returncode==0 else 'failed'
        record['queue_exit_code']=result.returncode
        # Queue text contains only our own task context; avoid persisting raw
        # CLI output, environment values, or unrelated daemon state.
    except subprocess.TimeoutExpired:record['delivery']='unknown_timeout'
    except OSError as exc:record.update(delivery='failed',error_type=type(exc).__name__)
    write_json(path,record)
    return True


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--interval',type=int,default=60)
    ap.add_argument('--once',action='store_true')
    ap.add_argument('--dry-run',action='store_true')
    args=ap.parse_args()
    if args.interval<15:ap.error('Minimum polling interval is 15 seconds.')
    folder=OUT/'event_watchdog';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'watchdog.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('A watchdog is already running.')
        read_errors=0
        while True:
            state=dict(pid=os.getpid(),updated_unix=time.time(),interval_seconds=args.interval,
                       mode='local_polling_without_model',scheduled_messages=False)
            try:
                s=snapshot();event=event_for(s)
                read_errors=0
                state.update(health='event' if event else 'normal',snapshot=s,event_key=event['key'] if event else None)
                completed=event and event['reason']=='finished_with_recorded_outcomes'
                if completed and not args.dry_run:
                    write_json(folder/'completion.json',dict(event,delivery='local_record_only_no_model'))
                    state['health']='completed'
                elif event and not args.dry_run:
                    state['new_event_dispatched']=dispatch(event,folder)
                if args.dry_run:print(json.dumps({'health':state['health'],'event':event['reason'] if event else None,'occupied_usd':s['occupied_usd']}))
            except Exception as exc:
                read_errors+=1
                state.update(health='local_read_error',error_type=type(exc).__name__)
                if read_errors>=3 and not args.dry_run:
                    event=dict(key='read-error-'+type(exc).__name__,reason='persistent_local_monitor_read_error',
                               detected_unix=time.time(),error_type=type(exc).__name__)
                    state['new_event_dispatched']=dispatch(event,folder)
            write_json(folder/'status.json',state)
            if args.once or state['health']=='completed':return
            time.sleep(args.interval)


if __name__=='__main__':main()
