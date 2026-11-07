import json
from pathlib import Path
import tempfile
import unittest

class ConcessionTests(unittest.TestCase):
    def test_quote_changes_only_first_ask_not_schedule_anchor(self):
        from seller_concession_trial import Trial
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p / "seller_concession_trial.json").write_text(json.dumps(dict(enabled=True,id="price",start=100,end=1900,seed=1)))
            t = Trial(p)
            deals = []
            for i in range(2):
                d = dict(id="sku",unit="u",kind="goods",cost=50000,reserve=80000,open=100000,ask=100000,history=[("you asked",100000)])
                t.quote(str(i),d,100)
                deals.append(d)
            self.assertEqual(sorted(d["ask"] for d in deals),[97000,100000])
            self.assertTrue(all(d["open"] == 100000 for d in deals))
            self.assertEqual(sorted(d["history"][0][1] for d in deals),[97000,100000])
            self.assertFalse((p/"seller_reader_trial.sqlite3").exists())

    def test_bound_sticky_stop_and_600_second_itt(self):
        from seller_concession_trial import Trial
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            cfg = dict(enabled=True,id='p',start=100,end=1900,seed=2)
            config = p/'seller_concession_trial.json'
            config.write_text(json.dumps(cfg))
            t=Trial(p)
            def deal():
                return dict(id='sku',unit='u',kind='goods',cost=50000,reserve=99000,open=100000,ask=100000,rounds=0,history=[('you asked',100000)])
            ds=[]
            for i in range(205):
                d=deal(); t.quote('full-uuid-'+str(i),d,100); ds.append(d)
            b=next(d for d in ds if d['concession_trial']['arm']=='B')
            self.assertEqual(b['ask'],99000)
            self.assertEqual(sum(d['concession_trial']['arm']=='B' for d in ds),100)
            a=ds[0]['concession_trial']
            self.assertEqual(Trial(p).assign('full-uuid-0',deal(),1901),a)
            self.assertIsNone(t.assign('new',deal(),1900)['id'])
            t.event('full-uuid-0',ds[0],'deal',100000,699)
            early=t.readout('p',699)
            self.assertEqual(sum(x['mature'] for x in early.values()),0)
            mature=t.readout('p',700)
            self.assertEqual(sum(x['deals'] for x in mature.values()),1)
            self.assertEqual(sum(x['gross_margin'] for x in mature.values()),50000)
            self.assertGreaterEqual(sum(x['mature'] for x in mature.values()),199)
            config.write_text(json.dumps(dict(cfg,stop=True)))
            t.restore(b)
            self.assertEqual(b['ask'],100000)
            config.write_text(json.dumps(dict(cfg,end=1901)))
            self.assertIsNone(t.assign('invalid',deal(),100)['id'])

    def test_default_off_and_invalid_restore(self):
        from seller_concession_trial import Trial
        with tempfile.TemporaryDirectory() as root:
            t=Trial(root)
            d=dict(open=100000,ask=100000,reserve=50000,history=[('you asked',100000)])
            t.quote('off',d,100)
            self.assertEqual(d['ask'],100000)
            self.assertFalse((Path(root)/'seller_concession_trial.sqlite3').exists())
