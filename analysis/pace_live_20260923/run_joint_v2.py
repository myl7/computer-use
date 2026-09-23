"""Preregistered GUI recovery and bill-budget component validation.

Credentials are loaded only by the budget client for the frozen experiment.
There are no model probes, retries, episode replays, or hidden evaluator reads
inside the action loop. The existing compiled program is a fixed artifact.
"""
from __future__ import annotations

import argparse
import ast
import base64
import contextlib
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "experimental-results/pace_live_20260923/v2"
sys.path.insert(0, str(ROOT / "code"))
from guiexp_android.recovery_validation_budget_v4 import (
    BudgetClient, BudgetLedger, BudgetError, validate_locks,
)

MODEL = "z-ai/glm-5.3-flash"
PROGRAM = ROOT / "experimental-results/guiexp/t13_compilepath/fullread/z-ai_glm-5.3-flash/single_page/attempt1/family_program.py"
TOKEN_CAP = 2048
STEP_CAP = 20
GLOBAL_USD_CAP = "5"
RESERVATION_NUSD = 40_345_600
REFERENCE_NUSD = 80_000_000
TIGHT_REFERENCE_NUSD = 20_000_000
SERVICE_CAP_NUSD = 80_000_000
COMPILER_CAP_NUSD = 60_000_000
COMPILER_TOKENS = 8192

PROMPT = """You operate a web calendar through the current screenshot and accessibility tree.
Finish the user's task from the CURRENT page, including any partially completed form.
Every turn return exactly one action, with no explanation or markdown:
click(bid), fill(bid, "text"), select(bid, "option"), scroll(up), scroll(down),
press("key"), or done(). Use current visible element bids from the latest observation.
fill replaces the field's content. Do not use source code, backend APIs, or hidden state.
Use visible controls to close obstructions. Avoid duplicate submissions.
Declare done() only after checking the visible result. Actions may reveal or scroll
to controls. A previous program may have filled some fields before it stopped.
You are not told whether that happened. Inspect the present page and preserve correct values.
"""

INJECT_JS = r"""(arm) => {
  const apply = () => {
    const form = document.querySelector('input[name="title"]')?.closest('form');
    if (!form) return;
    if (arm === 'renamed_submit') {
      for (const b of form.querySelectorAll('button,input[type="submit"]')) {
        if ((b.textContent || b.value || '').trim() === 'Submit') {
          b.textContent = 'Save event'; b.setAttribute('aria-label', 'Save event');
          window.__paceExposed = true;sessionStorage.setItem('paceExposed','true');
        }
      }
    }
    if (arm === 'blocking_dialog' && !window.__paceInjected) {
      const fields = ['title','date','description','url','invitees','location'];
      const filled = fields.filter(k => (form.querySelector(`[name="${k}"]`)?.value || '').trim()).length;
      if (filled >= 4) {
        window.__paceInjected = true; window.__paceExposed = true;sessionStorage.setItem('paceExposed','true');
        const overlay = document.createElement('div');
        overlay.id='pace-dialog'; overlay.setAttribute('role','dialog');
        overlay.setAttribute('aria-label','Calendar information');
        overlay.style='position:fixed;inset:0;z-index:2147483647;background:#eeeE;display:flex;align-items:center;justify-content:center';
        overlay.innerHTML='<div style="background:white;border:2px solid #333;padding:30px"><h2>Calendar information</h2><p>Close this message to continue editing your event.</p><button id="pace-close-dialog">Close message</button></div>';
        document.body.appendChild(overlay);
        overlay.querySelector('button').onclick=()=>{overlay.remove();window.__paceDismissed=true;sessionStorage.setItem('paceDismissed','true');};
      }
    }
  };
  new MutationObserver(apply).observe(document,{childList:true,subtree:true});
  document.addEventListener('input',apply,true);
  document.addEventListener('DOMContentLoaded',apply);
} """


def dump(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,indent=2,sort_keys=True,ensure_ascii=False,default=str)+'\n')
    tmp.replace(path)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def source_hashes():
    files=[Path(__file__),PROGRAM,ROOT/'code/guiexp/env.py',ROOT/'code/guiexp/actions.py',
           ROOT/'code/guiexp/program_runtime.py',ROOT/'code/guiexp/compiler.py',ROOT/'code/guiexp_android/recovery_validation_budget_v4.py',
           ROOT/'third-party/openapps/src/open_apps/apps/calendar_app/main.py']
    return {str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in files}


def prepare():
    if (OUT/'manifest.json').exists():
        raise RuntimeError('Manifest already exists. Never overwrite a frozen experiment.')
    raw=json.loads((HERE/'z-ai_glm-5.3-flash-endpoints.json').read_text())
    endpoint=next(e for e in raw['data']['endpoints'] if e['tag']=='io-net/fp8')
    endpoint=dict(endpoint,supports_image=True,
                  pricing={**endpoint['pricing'],'request':'0','image':'0'})
    locks={MODEL:{'model_id':MODEL,'context_length':262144,'endpoints':[endpoint]}}
    validate_locks(locks)
    tasks=[]
    arms=['clean','renamed_submit','blocking_dialog']
    for seed in range(4):
        for arm in arms:
            pair=f's{seed:02d}_{arm}'
            binding={'title':f'PACE {seed} {arm}', 'date':f'2026-09-{24+seed:02d}',
                     'description':f'Project review {seed}', 'location':f'Room {seed+1}',
                     'url':f'https://example.com/pace/{seed}', 'invitees':['Alice','Bob','Carol','Dennis'][seed]}
            tasks.append({'pair':pair,'seed':seed,'arm':arm,'binding':binding,
                          'order':['agent','program_fallback'] if seed%2==0 else ['program_fallback','agent']})
    spec={'schema':'pace-live-joint/2','frozen_at':datetime.now(timezone.utc).isoformat(),
          'model':MODEL,'locks':locks,'source_sha256':source_hashes(),'tasks':tasks,
          'max_tokens':TOKEN_CAP,'max_steps':STEP_CAP,'total_usd_cap':GLOBAL_USD_CAP,
          'reservation_nusd':RESERVATION_NUSD,'epsilon':'0.25',
          'reference_increment_nusd':REFERENCE_NUSD,'tight_increment_nusd':TIGHT_REFERENCE_NUSD,
          'service_total_cost_cap_nusd':SERVICE_CAP_NUSD,'compiler_total_cost_cap_nusd':COMPILER_CAP_NUSD,
          'compile_pilot':{'after_completed_agent_traces':3,'model_calls':1,'max_tokens':COMPILER_TOKENS,
                           'validation_seeds':[99,100],'repairs':0,'deployment_program_changes':False,
                           'trigger':'Prescheduled guard validation, not the PACE projected compilation trigger.',
                           'gate':'Two-instance pilot gate, not the main paper four-of-five admission gate.',
                           'charge_scope':'pace/acquisition prefix after the third matched pair',
                           'training_success_rule':'Keep all first three completed agent traces, including failure outcomes.'},
          'tight_stream':[dict(tasks[i],pair=f'tight_{i}') for i in [1,5,7,11]],
          'estimated_calls':'120-180; maximum 560; actual cost expected below USD 1; absolute ceiling USD 5',
          'role':'Deployment components plus a single cold-build pilot after three live agent traces. Fixed existing deployment program; no full online selection claim.',
          'reference':'Supplied per-arrival billing allowance, not measured agent-only bill or estimated true upper bound.',
          'budget_rule':'Before each paid request: stream exposure + context/output reservation <= 1.25 * declared reference prefix. Denial terminates task, counted unsuccessful.',
          'tight_rule':'Additional four program+fallback tasks at USD 0.02 per arrival, independent prefix ledger; same frozen program and perturbations.',
          'failure_rule':'No retries. Unknown billing stops all paid work, keeps worst-case reservation and every artifact. Parsing failure terminates task. GUI action error is observed by next call, until fixed 20-call cap.',
          'matching':'Fresh server and browser context before every arm; compare initial calendar state hashes and normalized AX hashes. No state reset before fallback.',
          'binding_cost':'Structured task fields are supplied directly to both policies. Model routing and binding extraction are outside this component experiment.',
          'success':'Only after policy termination or fixed cap: exactly one additional six-field matching event and no unrelated calendar change.',
          'perturbations':'Controlled synthetic label change and DOM blocking dialog after four fields are filled. Identical schedule applies to both policies.',
          'analysis':'All prespecified pairs; additionally report exposure-matched subset. Costs include unsuccessful/aborted calls. Means with paired bootstrap 95% intervals, seed clustering, and per-pair data.',
          'model_prompt':PROMPT,'perturbation_source':INJECT_JS}
    dump(OUT/'manifest.json',spec)
    print(json.dumps({'prepared':str(OUT/'manifest.json'),'pairs':len(tasks),'tight_tasks':4,'total_usd_cap':GLOBAL_USD_CAP}))


def load_spec():
    spec=json.loads((OUT/'manifest.json').read_text())
    if spec['source_sha256']!=source_hashes():raise RuntimeError('Frozen source changed')
    return spec


@contextlib.contextmanager
def server(directory):
    directory.mkdir(parents=True,exist_ok=True)
    log=(directory/'server.log').open('w')
    env=dict(os.environ)
    env['PYTHONPATH']=str(ROOT/'third-party/openapps/src')
    command=[str(ROOT/'third-party/openapps/.venv/bin/python'),str(ROOT/'third-party/openapps/launch.py'),
             'apps/calendar/appearance=default',f'logs_dir={directory}/server-data',f'hydra.run.dir={directory}/server-hydra']
    p=subprocess.Popen(command,cwd=ROOT/'third-party/openapps',env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        end=time.monotonic()+60
        while time.monotonic()<end:
            if p.poll() is not None:raise RuntimeError('Local server exited; see server.log')
            text=(directory/'server.log').read_text()
            match=re.search(r'Uvicorn running on http://[^:]+:(\d+)',text)
            if match:
                yield 'http://127.0.0.1:'+match.group(1)
                return
            time.sleep(.2)
        raise RuntimeError('Local server startup timeout')
    finally:
        try:os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=10)
        except ProcessLookupError:pass
        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=10)
        log.close()


def get_state(base):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base+'/calendar_all',timeout=10) as r:return json.load(r)


def state_rows(state):
    if isinstance(state,list):return state
    if isinstance(state,dict):
        for key in ['events','data','calendar']:
            if isinstance(state.get(key),list):return state[key]
    raise RuntimeError('Unexpected calendar evaluator schema')


def cost_of(ledger,prefix):
    rows=[r for r in ledger.records() if r['episode'].startswith(prefix)]
    return sum(r['actual_cost_nusd'] if r['state']=='settled' else max(r['reservation_nusd'],r['actual_cost_nusd'] or 0) for r in rows)


class PrefixDenied(RuntimeError):pass


def make_env(base):
    from guiexp.env import GuiEnv
    from browsergym.core.observation import (_pre_extract,_post_extract,extract_dom_snapshot,
        extract_merged_axtree,extract_dom_extra_properties)
    from browsergym.utils.obs import flatten_axtree_to_str
    class CurrentGuiEnv(GuiEnv):
        def _observe(self):
            self._active_page_check();_pre_extract(self.page)
            try:
                dom=extract_dom_snapshot(self.page);tree=extract_merged_axtree(self.page)
                extra=extract_dom_extra_properties(dom)
                text=flatten_axtree_to_str(tree,extra_properties=extra,with_visible=True,
                    with_clickable=True,filter_visible_only=True,filter_with_bid_only=True)
                return {'screenshot_b64':base64.b64encode(self.page.screenshot()).decode(),
                        'ax_tree_text':text,'url':self.page.url}
            finally:_post_extract(self.page)
        def reset(self,task):
            # Initialization only. Do not query success inside action execution.
            self.task=task;self.page.goto(task.start_url,wait_until='domcontentloaded',timeout=10000);return self._observe()
    env=CurrentGuiEnv(base,layout='single_page')
    env._context.set_default_timeout(1800)
    env._context.set_default_navigation_timeout(10000)
    return env


def save_observation(env,path,step):
    obs=env._observe();stem=f'observation_{step:03d}'
    (path/(stem+'.png')).write_bytes(base64.b64decode(obs['screenshot_b64']))
    dump(path/(stem+'.json'),{k:v for k,v in obs.items() if not k.endswith('b64') and k not in ['extra_element_properties','dom_snapshot']})
    return obs


def run_agent(env,client,path,episode,goal,stream,t,increment):
    from guiexp.actions import execute,parse_action
    history=[];events=[];error=None
    for step in range(STEP_CAP):
        obs=save_observation(env,path,step)
        text=goal+'\nCurrent URL: '+obs['url']+'\n'+obs['ax_tree_text']
        if error:text+='\nPrevious action error: '+error
        messages=[{'role':'system','content':PROMPT},*history,{'role':'user','content':[
            {'type':'text','text':text},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+obs['screenshot_b64']}}]}]
        service_used=cost_of(client.ledger,episode)
        if service_used+RESERVATION_NUSD>SERVICE_CAP_NUSD:
            events.append({'episode_budget_denied':{'used_nusd':service_used,'reserve_nusd':RESERVATION_NUSD,'cap_nusd':SERVICE_CAP_NUSD}})
            dump(path/'trajectory.json',events)
            return {'termination':'service_cost_cap','calls':step}
        if stream:
            used=cost_of(client.ledger,stream+'/');limit=increment*t*5//4
            admission={'step':step,'exposure_nusd':used,'reservation_nusd':RESERVATION_NUSD,'ceiling_nusd':limit,'admitted':used+RESERVATION_NUSD<=limit}
            events.append({'budget_admission':admission});dump(path/'trajectory.json',events)
            if not admission['admitted']:raise PrefixDenied('Next complete request reservation exceeds prefix allowance')
        response=client.complete(MODEL,messages,TOKEN_CAP,episode,temperature=0,reasoning_effort='low')
        choice=response['choices'][0];reply=choice.get('message',{}).get('content') or ''
        entry={'step':step,'generation_id':response['id'],'response':response,'error':None}
        events.append(entry);dump(path/'trajectory.json',events)
        if choice.get('finish_reason')!='stop':return {'termination':'non_stop_response','calls':step+1}
        try:action=parse_action(re.sub(r'^action:\s*','',reply.strip()))
        except Exception:return {'termination':'parse_failure','calls':step+1}
        if action.name=='done':return {'termination':'done','calls':step+1}
        if action.name=='goto':return {'termination':'disallowed_action','calls':step+1}
        try:
            env._execute_action(action);env.page.wait_for_timeout(250)
            env._active_page_check();error=None
        except Exception as exc:error=f'{type(exc).__name__}: {str(exc).splitlines()[0][:150]}'
        entry['action']=action.render();entry['error']=error;dump(path/'trajectory.json',events)
        # Only the current screenshot is sent. Prior observations remain as text.
        history.extend([{'role':'user','content':text},{'role':'assistant','content':reply}])
    return {'termination':'step_cap','calls':STEP_CAP}


def episode(spec,client,task,policy,index,tight=False):
    from guiexp.env import Task
    from guiexp.program_runtime import program_from_path,_ActivePageProxy
    stream='tight' if tight else ('pace' if policy=='program_fallback' else None)
    label=f'{stream or "agent"}/{task["pair"]}/{policy}'
    path=OUT/'episodes'/label
    if (path/'started.json').exists():raise RuntimeError('Refuse replay of started episode')
    path.mkdir(parents=True,exist_ok=True)
    dump(path/'started.json',{'episode':label,'time':time.time(),'manifest_sha256':sha((OUT/'manifest.json').read_bytes())})
    before_cost=cost_of(client.ledger,label)
    result={'episode':label,'pair':task['pair'],'policy':policy,'arm':task['arm'],'seed':task['seed'],
            'prefix_index':index,'binding':task['binding'],'program_error':None,'fallback':False,'success':False}
    fatal=None
    with server(path) as base:
        env=make_env(base);env.page.set_default_timeout(1800)
        env._context.add_init_script('('+INJECT_JS+')('+json.dumps(task['arm'])+');')
        holder={'page':env.page};env._context.on('page',lambda page:holder.update(page=page))
        try:
            b=task['binding'];obs=env.reset(Task('single_page','discover',task['seed'],b,base+'/calendar'))
            before=get_state(base);dump(path/'initial-state.json',before)
            result['initial_state_sha256']=sha(json.dumps(before,sort_keys=True).encode())
            result['initial_ax_sha256']=sha(obs['ax_tree_text'].replace(base,'BASE').encode())
            save_observation(env,path,100)
            program_ok=False
            if policy=='program_fallback':
                _,program=program_from_path(PROGRAM)
                try:
                    program(_ActivePageProxy(holder),dict(b),base)
                    env.page=holder['page'];program_ok=True
                    result['termination']='program_return'
                except Exception as exc:
                    env.page=holder['page']
                    result['program_error']=f'{type(exc).__name__}: {str(exc).splitlines()[0][:200]}'
                    snap=save_observation(env,path,101)
                    result['fallback_start_url']=snap['url']
                    result['fallback_form_values']=env.page.locator('input,textarea').evaluate_all('(xs)=>xs.map(x=>({name:x.name,value:x.value}))')
            if not program_ok:
                result['fallback']=policy=='program_fallback'
                goal='Create exactly one calendar event with these values: '+json.dumps(b,ensure_ascii=False)
                try:result.update(run_agent(env,client,path,label,goal,stream,index,TIGHT_REFERENCE_NUSD if tight else REFERENCE_NUSD))
                except PrefixDenied as exc:result.update(termination='prefix_denied',error=str(exc))
            # Evaluator is first consulted after the policy has stopped.
            after=get_state(base);dump(path/'final-state.json',after)
            initial=state_rows(before);final=state_rows(after)
            matches=lambda r:all(str(r.get(k) or '')==v for k,v in b.items())
            result['matching_event_delta']=sum(map(matches,final))-sum(map(matches,initial))
            result['event_count_delta']=len(final)-len(initial)
            known_ids={r['id']:r for r in initial}
            final_old={r['id']:r for r in final if r['id'] in known_ids}
            result['unrelated_state_preserved']=known_ids==final_old
            result['success']=result['matching_event_delta']==1 and result['event_count_delta']==1 and result['unrelated_state_preserved']
            result['perturbation_exposed']=bool(env.page.evaluate("sessionStorage.getItem('paceExposed')==='true'"))
            result['dialog_dismissed']=bool(env.page.evaluate("sessionStorage.getItem('paceDismissed')==='true'"))
            save_observation(env,path,102)
        except BudgetError as exc:
            result.update(termination='billing_stop',error=f'{type(exc).__name__}: {exc}');fatal=exc
        except Exception as exc:
            result.update(termination='infrastructure_error',error=f'{type(exc).__name__}: {str(exc).splitlines()[0][:200]}');fatal=exc
        finally:
            result['cost_nusd']=cost_of(client.ledger,label)-before_cost
            if stream:
                result['prefix_cost_nusd']=cost_of(client.ledger,stream+'/')
                result['reference_prefix_nusd']=index*(TIGHT_REFERENCE_NUSD if tight else REFERENCE_NUSD)
                result['prefix_ceiling_nusd']=result['reference_prefix_nusd']*5//4
                result['prefix_bound_holds']=result['prefix_cost_nusd']<=result['prefix_ceiling_nusd']
            result['end_time']=time.time();dump(path/'result.json',result)
            env.close()
    print(json.dumps({k:result.get(k) for k in ['episode','success','fallback','termination','cost_nusd','error']}),flush=True)
    if fatal:raise fatal
    return result


def run(spec):
    client=BudgetClient(BudgetLedger(OUT/'budget.sqlite3',GLOBAL_USD_CAP),spec['locks'],ROOT.parent/'.env')
    results=[]
    for index,task in enumerate(spec['tasks'],1):
        for policy in task['order']:
            results.append(episode(spec,client,task,policy,index))
            dump(OUT/'progress.json',{'results':results,'budget':client.ledger.summary()})
        pair=results[-2:]
        if pair[0]['initial_state_sha256']!=pair[1]['initial_state_sha256'] or pair[0]['initial_ax_sha256']!=pair[1]['initial_ax_sha256']:
            raise RuntimeError('Matched initial state mismatch; stop before additional paid calls')
        if index==3:compile_pilot(spec,client,results)
    for index,task in enumerate(spec['tight_stream'],1):
        results.append(episode(spec,client,task,'program_fallback',index,tight=True))
        dump(OUT/'progress.json',{'results':results,'budget':client.ledger.summary()})
    print(json.dumps(client.ledger.summary()),flush=True)


def validate_candidate_source(source):
    source=re.sub(r'^```(?:python)?\s*|\s*```$','',source.strip())
    tree=ast.parse(source)
    forbidden={'open','exec','eval','compile','globals','locals','getattr','setattr','vars','input','__import__'}
    for node in ast.walk(tree):
        if isinstance(node,ast.Name) and (node.id in forbidden or node.id.startswith('__')):raise ValueError('Forbidden name')
        if isinstance(node,ast.Attribute) and (node.attr.startswith('_') or node.attr in ['request','route','evaluate','evaluate_all','add_init_script','expose_binding','context']):raise ValueError('Forbidden attribute')
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            names=[n.name for n in node.names] if isinstance(node,ast.Import) else [node.module]
            if any(n not in ['re','datetime','time','math'] for n in names):raise ValueError('Forbidden import')
    return source


def compile_pilot(spec,client,results):
    from guiexp.env import Task
    from guiexp.program_runtime import program_from_source,_ActivePageProxy
    from guiexp.compiler import _CODE_CONTRACT_LINES,COMPILE_SYSTEM_PROMPT
    path=OUT/'acquisition';path.mkdir(parents=True,exist_ok=True)
    completed=[r for r in results if r['policy']=='agent'][:3]
    traces=[]
    for result in completed:
        ep=OUT/'episodes'/result['episode'];events=json.loads((ep/'trajectory.json').read_text())
        trace={'goal_binding':result['binding'],'outcome':result,'steps':[]}
        for ev in events:
            if 'step' not in ev:continue
            obs=json.loads((ep/f'observation_{ev["step"]:03d}.json').read_text())
            trace['steps'].append({'observation':obs,'action':ev.get('action'),'response':ev.get('response',{}).get('choices',[{}])[0].get('message',{}).get('content'),'error':ev.get('error')})
        traces.append(trace)
    messages=[{'role':'system','content':COMPILE_SYSTEM_PROMPT},{'role':'user','content':'Compile the following three completed agent runs into one calendar family program.\n'+json.dumps(traces,ensure_ascii=False)+'\n'+'\n'.join(_CODE_CONTRACT_LINES)}]
    dump(path/'input-traces.json',traces)
    result={'training_traces':len(traces),'attempts':0,'validation':[],'accepted':False}
    used=cost_of(client.ledger,'pace/');ceiling=3*REFERENCE_NUSD*5//4
    result['admission']={'prefix_used_nusd':used,'ceiling_nusd':ceiling,'compiler_cap_nusd':COMPILER_CAP_NUSD,'admitted':used+COMPILER_CAP_NUSD<=ceiling}
    dump(path/'result.json',result)
    if not result['admission']['admitted']:return
    try:
        if 262144*150+COMPILER_TOKENS*500>COMPILER_CAP_NUSD:raise RuntimeError('Compiler total budget cannot cover its call')
        result['attempts']=1
        response=client.complete(MODEL,messages,COMPILER_TOKENS,'pace/acquisition/compile',temperature=0,reasoning_effort='low')
        dump(path/'response.json',response)
        result['finish_reason']=response['choices'][0].get('finish_reason')
        if result['finish_reason']!='stop':raise ValueError('Compiler did not finish normally')
        source=validate_candidate_source(response['choices'][0]['message'].get('content') or '')
        (path/'candidate.py').write_text(source)
        _,program=program_from_source(source)
        for seed in [99,100]:
            case=path/f'validation_s{seed}';record={'seed':seed,'success':False}
            with server(case) as base:
                env=make_env(base);env.page.set_default_timeout(1800)
                holder={'page':env.page};env._context.on('page',lambda page:holder.update(page=page))
                b={'title':f'PACE cold build {seed}','date':'2026-09-28','description':f'Validation {seed}',
                   'location':'Room 9','url':'https://example.com/build','invitees':'Alice'}
                try:
                    env.reset(Task('single_page','discover',seed,b,base+'/calendar'));before=get_state(base)
                    save_observation(env,case,0)
                    def expired(*_):raise TimeoutError('Candidate validation reached 120 second limit')
                    old_alarm=signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,120)
                    try:program(_ActivePageProxy(holder),dict(b),base)
                    finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old_alarm)
                    env.page=holder['page']
                    after=get_state(base);save_observation(env,case,1)
                    match=lambda r:all(str(r.get(k) or '')==v for k,v in b.items())
                    known_ids={r['id']:r for r in state_rows(before)}
                    final_old={r['id']:r for r in state_rows(after) if r['id'] in known_ids}
                    record['unrelated_state_preserved']=known_ids==final_old
                    record['success']=sum(map(match,state_rows(after)))-sum(map(match,state_rows(before)))==1 and len(state_rows(after))==len(state_rows(before))+1 and record['unrelated_state_preserved']
                    dump(case/'before.json',before);dump(case/'after.json',after)
                except Exception as exc:record['error']=f'{type(exc).__name__}: {str(exc).splitlines()[0][:160]}'
                finally:env.close()
            result['validation'].append(record);dump(path/'result.json',result)
        result['accepted']=all(r['success'] for r in result['validation'])
    except BudgetError:
        result['error']='Billing or global budget stopped acquisition';raise
    except Exception as exc:result['error']=f'{type(exc).__name__}: {str(exc).splitlines()[0][:200]}'
    finally:
        result['cost_nusd']=cost_of(client.ledger,'pace/acquisition/')
        result['prefix_cost_nusd']=cost_of(client.ledger,'pace/')
        result['prefix_ceiling_nusd']=ceiling
        result['prefix_bound_holds']=result['prefix_cost_nusd']<=ceiling
        dump(path/'result.json',result)
    print(json.dumps({'acquisition':result}),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','run','smoke'])
    args=parser.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.mode=='prepare':prepare()
        elif args.mode=='run':run(load_spec())
        else:
            with server(HERE/'fixture-smoke') as url:
                print(url,json.dumps(get_state(url))[:150])


if __name__=='__main__':main()
