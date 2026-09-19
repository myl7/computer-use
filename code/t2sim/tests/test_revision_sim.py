"""Behavioral checks for the revised experimental protocol."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import revision_sim as sim


class RevisionProtocolTests(unittest.TestCase):
    def run_case(self,stream,policy='earliest',**kw):
        profile=dict(c=10.0,d=1.0,C=5.0,C_fail=7.0,p=1.0,q=0.0,pi=1.0)
        profile.update(kw.pop('profile',{}))
        return sim.run(stream,{'template':profile},{s:'template' for s in stream},
                       policy,h=kw.pop('h',0),m=0,tau0=0,trace=True,**kw)

    def test_three_paid_demonstrations_before_first_program_use(self):
        r=self.run_case(['f']*4)
        self.assertEqual([e['program'] for e in r['events']],[False,False,False,True])
        self.assertEqual(r['tokens'],36)
        self.assertEqual(r['attempts'],1)

    def test_terminal_arrival_can_incure_compile_charge(self):
        r=self.run_case(['f']*3)
        self.assertEqual(r['tokens'],35)
        self.assertEqual(r['program_uses'],0)

    def test_failed_attempts_charge_own_price(self):
        r=self.run_case(['f']*5,profile={'p':0})
        self.assertEqual(r['tokens'],50+3*7)
        self.assertEqual(r['failed_compile_tokens'],21)
        self.assertEqual(sim.expected_buy(5,7,.5),12)

    def test_success_count_requires_successful_reactive_episodes(self):
        r=self.run_case(['f']*30,'success10_cap',profile={'pi':0,'C_fail':0})
        self.assertEqual(r['attempts'],0)
        r=self.run_case(['f']*11,'success10_cap',profile={'C_fail':0})
        self.assertTrue(r['events'][9]['attempt'])
        self.assertTrue(r['events'][10]['program'])

    def test_drift_fallback_is_counted_once_and_is_a_demonstration(self):
        r=self.run_case(['f']*4,h=1)
        self.assertEqual(r['reactive_uses'],4)
        self.assertEqual(r['events'][3]['service_cost'],11)
        self.assertEqual(r['events'][3]['demos'],4)
        self.assertEqual(r['breaks'],1)

    def test_cap_reserves_the_next_failure(self):
        r=self.run_case(['f']*6,'earliest_cap',profile={'C_fail':1000,'p':0})
        self.assertEqual(r['attempts'],0)

    def test_free_relisting_does_not_recompile(self):
        r=self.run_case(['f']*3+['g']*3+['f'],ttl=1)
        self.assertEqual(r['admissions'],2)
        self.assertEqual(r['relists'],1)
        self.assertTrue(r['events'][-1]['program'])

    def test_silent_failure_has_no_free_fallback(self):
        r=self.run_case(['f']*4,profile={'q':1},silent=1,penalty=3)
        self.assertEqual(r['reactive_uses'],3)
        self.assertEqual(r['successes'],3)
        self.assertEqual(r['harm_tokens'],30)

    def test_same_task_reactive_outcome_is_shared_across_policies(self):
        a=self.run_case(['f']*12,'earliest',profile={'q':1,'pi':.5})
        b=self.run_case(['f']*12,'reactive',profile={'pi':.5})
        self.assertEqual([e['success'] for e in a['events']],
                         [e['success'] for e in b['events']])

    def test_hidden_harm_does_not_enter_observed_savings(self):
        a=self.run_case(['f']*6,profile={'q':1},silent=1,penalty=1)
        b=self.run_case(['f']*6,profile={'q':1},silent=1,penalty=30)
        self.assertEqual([e['controller_saving'] for e in a['events']],
                         [e['controller_saving'] for e in b['events']])
        self.assertNotEqual(a['harm_tokens'],b['harm_tokens'])

    def test_admission_estimate_does_not_read_true_probability(self):
        a=self.run_case(['f']*3,profile={'p':0})
        b=self.run_case(['f']*3,profile={'p':1})
        self.assertEqual(a['events'][-1]['p_est'],b['events'][-1]['p_est'])
        self.assertEqual(a['events'][-1]['expected_buy'],b['events'][-1]['expected_buy'])


if __name__=='__main__':unittest.main()
