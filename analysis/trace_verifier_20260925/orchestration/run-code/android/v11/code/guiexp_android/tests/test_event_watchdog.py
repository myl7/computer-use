import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

path=Path(__file__).resolve().parents[1]/'event_watchdog.py'
spec=importlib.util.spec_from_file_location('watchdog_test',path)
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)

class WatchdogTests(unittest.TestCase):
    def state(self,**kw):
        s=dict(now=1000,batch={'pid':1,'started_unix':0},counts={'pending':10},
               supervisor_alive=True,worker_alive=True,run_lock_held=True,
               latest_request=('call-1','reserved',900),active_episode_started=1,
               occupied_usd=3,overrun=False,repair_in_progress=False)
        s.update(kw);return s
    def test_normal_and_bounded_wait_do_not_trigger(self):
        self.assertIsNone(w.event_for(self.state()))
        self.assertIsNone(w.event_for(self.state(latest_request=('c','uncertain',600))))
    def test_supervisor_heartbeat_does_not_hide_stalled_worker(self):
        self.assertEqual(w.event_for(self.state(latest_request=('c','settled',10)))['reason'],'no_progress_15_minutes')
    def test_stop_and_complete_are_events(self):
        s=self.state(supervisor_alive=False,worker_alive=False,run_lock_held=False)
        self.assertEqual(w.event_for(s)['reason'],'batch_stopped')
        s['counts']={'done':100,'censored_prior':10}
        self.assertEqual(w.event_for(s)['reason'],'finished_with_recorded_outcomes')
    def test_partial_supervisor_failure_does_not_duplicate_worker(self):
        self.assertIsNone(w.event_for(self.state(supervisor_alive=False)))
    def test_active_repair_suppresses_duplicate_requests(self):
        self.assertIsNone(w.event_for(self.state(repair_in_progress=True,latest_request=('c','settled',10))))
    def test_budget_is_actionable(self):
        self.assertEqual(w.event_for(self.state(occupied_usd=11))['reason'],'budget_overrun')
    def test_budget_overrun_is_not_hidden_by_repair_flag(self):
        self.assertEqual(w.event_for(self.state(repair_in_progress=True,occupied_usd=11))['reason'],'budget_overrun')
    def test_each_incident_queues_once(self):
        calls=[]
        def sender(*a,**kw):calls.append(a);return SimpleNamespace(returncode=0)
        event=w.event_for(self.state(supervisor_alive=False,worker_alive=False,run_lock_held=False))
        with TemporaryDirectory() as d:
            self.assertTrue(w.dispatch(event,Path(d),sender))
            self.assertFalse(w.dispatch(event,Path(d),sender))
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0][1],'queue')
    def test_uncertain_delivery_is_not_retried(self):
        def sender(*a,**kw):raise w.subprocess.TimeoutExpired('queue',60)
        e=w.event_for(self.state(overrun=True))
        with TemporaryDirectory() as d:
            self.assertTrue(w.dispatch(e,Path(d),sender))
            self.assertFalse(w.dispatch(e,Path(d),sender))

if __name__=='__main__':unittest.main()
