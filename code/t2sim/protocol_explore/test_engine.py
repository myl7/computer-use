import math
from pathlib import Path
import random
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import engine

class SafetyTests(unittest.TestCase):
    def profile(self,**kw):
        x=dict(c=10.,d=1.,C=5.,C_fail=7.,q=.05,p=.6,pi=.8);x.update(kw);return x
    def run_case(self,seq,policy=None,profile=None,**kw):
        return engine.run(seq,{'p':profile or self.profile()},{f:'p' for f in seq},
                          policy or engine.Policy('test','earliest',.25),
                          trace=True,m=kw.pop('m',0),tau0=kw.pop('tau0',0),**kw)
    def test_unprotected_matches_reference(self):
        seq=random.Random(11).choices(['a','b','c'],k=120)
        profiles={'p':self.profile()};mapping={f:'p' for f in seq}
        for kind in ['earliest','projected']:
            a=engine.reference.run(seq,profiles,mapping,kind,seed=19,h=.1,silent=.3,
                                   fallback_mult=2,m=3,tau0=2)
            b=engine.run(seq,profiles,mapping,engine.Policy('test',kind,None),
                         seed=19,h=.1,silent=.3,fallback_mult=2,m=3,tau0=2)
            for k,v in a.items():
                self.assertAlmostEqual(v,b[k],places=8,msg=f'{kind}/{k}')
    def test_reserve_success_cost_when_larger(self):
        r=self.run_case(['a']*3,profile=self.profile(C=100,C_fail=1,p=1),h=0)
        self.assertEqual(r['attempts'],0)
    def test_first_program_use_is_after_three_completed_runs(self):
        r=self.run_case(['a']*4,profile=self.profile(C=1,C_fail=1,p=1,q=0),h=0)
        self.assertEqual([e['program'] for e in r['events']],[False,False,False,True])
    def test_router_can_be_removed_before_charge(self):
        r=self.run_case(['a']*12,profile=self.profile(C=1,C_fail=1,p=1,q=0),h=0,m=1000)
        self.assertGreater(r['safety_service_fallbacks'],0)
        self.assertLessEqual(r['max_prefix_ratio'],1.25000001)
    def test_failure_fallback_and_all_costs_are_reserved(self):
        r=self.run_case(['a','b']*100,profile=self.profile(C=2,C_fail=50,p=.4,q=.7),h=.2,
                        fallback_mult=3,m=2,tau0=4,silent=.2)
        self.assertLessEqual(r['max_prefix_ratio'],1.25000001)
        self.assertAlmostEqual(r['token_cost'],sum(r[k] for k in ['reactive_tokens','extraction_tokens','compile_tokens','router_tokens']))
    def test_no_true_p_or_future_in_proposal(self):
        fields=dict(demos=3,alive=False,arrivals=3,successes_since_admission=3,
                    attempts=0,admits=0,age=3,c=10,d=1,C=4,Cf=40,q=0,h=.02,
                    m=0,ttl=100,silent=0,penalty=3,fallback_mult=1,k_min=3)
        self.assertTrue(engine.propose('optimistic',**fields))
        fields['true_p']=.9
        with self.assertRaises(TypeError):engine.propose('optimistic',**fields)
    def test_cp_bounds(self):
        self.assertEqual(engine.upper_probability(0,0),1)
        self.assertEqual(engine.upper_probability(3,3),1)
        self.assertAlmostEqual(engine.upper_probability(0,1),.9)
        self.assertAlmostEqual(engine.upper_probability(0,2),1-math.sqrt(.1))
        self.assertTrue(.8 < engine.upper_probability(1,2) < 1)
    def test_fast_beta_matches_exact_binomial_tail(self):
        for n in range(2,21):
            for g in range(1,n):
                p=engine.upper_probability(g,n)
                tail=sum(math.comb(n,j)*p**j*(1-p)**(n-j) for j in range(g+1))
                self.assertAlmostEqual(tail,.1,places=10)
        self.assertTrue(.9<engine.upper_probability(9000,10000)<.92)
    def test_adversarial_price_horizon_grid_satisfies_budget(self):
        for eps in [0,.1,.25,.5,1]:
            for p in [0,.3,1]:
                for C,Cf in [(1,100),(100,1),(100,100)]:
                    r=self.run_case(['a']*30,engine.Policy('test','optimistic',eps),
                                    self.profile(C=C,C_fail=Cf,p=p),h=.1,m=1,tau0=1)
                    self.assertLessEqual(r['max_prefix_ratio'],1+eps+1e-8)
    def test_terminal_compile_charge_is_not_forgiven(self):
        r=self.run_case(['a']*3,profile=self.profile(C=5,C_fail=5,p=1,q=0),h=0)
        self.assertEqual(r['compile_tokens'],5)
        self.assertEqual(r['program_uses'],0)
    def test_unknown_p_worlds_have_same_first_attempt_timing(self):
        out=[]
        for p in [0,1]:
            r=self.run_case(['a']*120,profile=self.profile(C=1,C_fail=100,p=p,q=0),h=0)
            out.append(next(e['t'] for e in r['events'] if e['attempt']))
        self.assertEqual(out[0],out[1])
    def test_harm_is_not_token_credit(self):
        a=self.run_case(['a']*30,profile=self.profile(C=1,C_fail=1,p=1,q=1),
                        h=0,silent=1,penalty=3)
        self.assertGreater(a['harm_tokens'],0)
        self.assertAlmostEqual(a['tokens'],a['token_cost']+a['harm_tokens'])
        self.assertLessEqual(a['max_prefix_ratio'],1.25000001)

if __name__=='__main__':unittest.main()
