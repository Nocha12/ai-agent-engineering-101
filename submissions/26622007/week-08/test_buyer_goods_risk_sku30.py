"""Goods-only risk SKU choice: finite dispatch and original negotiation guards."""
import unittest
import copy
import math
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import buyer_goods_risk_sku30 as trial

class RiskSkuCandidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.md=self.tmp.name
        self.cfg=dict(enabled=True,study='risk-test',salt='risk-test',start=2000000000,end=2000001800)
        self.high=dict(sku='G11111111',name='fixture high',list_price=10000,qty=1)
        self.low=dict(sku='G22222222',name='fixture low',list_price=8000,qty=1)
        self.shops=[dict(name='fixture',url='fixture',usable=[self.high,self.low])]
        self.oid=next(str(i) for i in range(100) if trial.arm(self.cfg,str(i))=='B')
        self.order=dict(kind='goods',order=self.oid,_buyer_started_at=2000000001)
        self.base=lambda offers,budget:max(offers,key=lambda o:o['list_price'])
        self.risk=lambda sku:.15 if sku==self.high['sku'] else .09

    def flag(self,**changes):
        p=Path(self.md)/trial.SWITCH;p.write_text(json.dumps(self.cfg|changes))
        os.utime(p,(1999999990,1999999990))

    def reserve(self,order=None,shops=None,**kwargs):
        return trial.reserve(self.md,self.order if order is None else order,
            self.shops if shops is None else shops,12000,self.base,self.risk,now=2000000001,**kwargs)

    def test_off_and_food_do_not_scan_models_or_create_journal_or_log_fields(self):
        for kind in ('goods','food'):
            order=dict(self.order,kind=kind)
            with patch.object(trial,'candidate',side_effect=AssertionError('OFF model call')):
                self.assertIsNone(self.reserve(order=order))
            self.assertIsNone(trial.measurement(order))
        self.assertEqual(list(Path(self.md).iterdir()),[])
        self.flag()
        self.assertIsNone(self.reserve(order=dict(self.order,kind='food')))
        self.assertEqual(len(list(Path(self.md).iterdir())),1)

    def test_generator_and_reader_enforce_exact_future_window(self):
        with patch.object(trial.time,'time',return_value=1999999990):
            for change in ({'end':2000003600},{'start':float('nan')},{'end':float('inf')},{'start':True}):
                cfg={k:self.cfg[k] for k in ('study','salt','start','end')}|change
                with self.assertRaises(ValueError):trial.switch_on(self.md,**cfg)
            trial.switch_on(self.md,**{k:self.cfg[k] for k in ('study','salt','start','end')})
        p=Path(self.md)/trial.SWITCH;os.utime(p,(1999999990,1999999990))
        self.assertIsNone(trial.config(self.md,1999999999))
        self.assertIsNotNone(trial.config(self.md,2000000000))
        self.assertIsNone(trial.config(self.md,2000001800))

    def test_reader_rejects_nan_inf_bool_bad_shape_and_wrong_duration(self):
        for change in ({'start':float('nan')},{'end':float('inf')},{'enabled':1},{'start':True},
                       {'end':2000001801},{'study':'../bad'},{'salt':None}):
            self.flag(**change)
            self.assertIsNone(trial.config(self.md,2000000001),change)

    def test_preexisting_started_window_and_other_trials_fail_closed(self):
        self.flag()
        for value in (1999999999,2000001800,float('nan'),float('inf'),None,True):
            self.assertIsNone(self.reserve(order=dict(self.order,_buyer_started_at=value)))
        for key in ('_goods_take30','_stock_evidence','_goods_extra_quote_probe',
                    '_quote_alignment','_our_ask','_moss_first'):
            self.assertIsNone(self.reserve(order=dict(self.order,**{key:{'arm':'B'}})))
        self.assertEqual(len(list(Path(self.md).iterdir())),1)

    def test_changed_inventory_or_switched_off_before_dispatch_keeps_baseline(self):
        self.flag()
        with patch.object(trial.time,'time',return_value=2000000001):
            self.reserve()
            self.shops[0]['usable'].append(dict(self.low,sku='G33333333'))
            self.assertEqual(trial.dispatch(self.shops[0],12000,self.base),self.high)
            self.assertFalse(trial.measurement(self.order)['applied'])
        # No new B on an order already seen, including a failed partial snapshot.
        self.assertIsNone(self.reserve(order=dict(self.order)))
        self.assertEqual(self.order[trial.KEY]['arm'],'B')  # retry dict did not mutate first assignment

    def test_disabled_between_plan_and_dispatch_and_end_boundary_never_treat(self):
        for at,disable in ((2000001800,False),(2000000001,True)):
            with tempfile.TemporaryDirectory() as md:
                cfg=self.cfg
                p=Path(md)/trial.SWITCH;p.write_text(json.dumps(cfg));os.utime(p,(1999999990,1999999990))
                shops=copy.deepcopy(self.shops);order=dict(self.order)
                trial.reserve(md,order,shops,12000,self.base,self.risk,now=2000000001)
                if disable:p.write_text(json.dumps(cfg|{'enabled':False}))
                with patch.object(trial.time,'time',return_value=at):
                    self.assertEqual(trial.dispatch(shops[0],12000,self.base),self.high)
                self.assertFalse(trial.measurement(order)['applied'])

    def test_unknown_overbudget_outofstock_and_equal_or_worse_risk_never_replace(self):
        for offers,chance,budget in (([self.high,self.low],lambda sku:None,12000),
                    ([self.high,self.low],lambda sku:.15,12000),
                    ([self.high,self.low],lambda sku:.15 if sku==self.high['sku'] else .2,12000),
                    ([self.high,dict(self.low,qty=0)],self.risk,12000),
                    ([self.high,self.low],self.risk,7000)):
            self.assertIsNone(trial.candidate(offers,self.high,budget,chance))

    def test_a_assignment_and_no_alternative_are_untreated(self):
        self.flag()
        oid=next(str(i) for i in range(100) if trial.arm(self.cfg,str(i))=='A')
        order=dict(self.order,order=oid)
        with patch.object(trial.time,'time',return_value=2000000001):
            self.reserve(order=order)
            self.assertEqual(trial.dispatch(self.shops[0],12000,self.base),self.high)
        self.assertEqual(trial.measurement(order)['arm'],'A')
        self.assertFalse(trial.measurement(order)['applied'])

    def test_actual_b_excluded_from_rank_profile_and_paid_rates(self):
        import buyer_rank_pg as rp
        import buyer_shop_profile as sp
        import buyer_b3b1 as b1
        from test_buyer_rank_pg import row, NOW
        from test_buyer_shop_profile import holder
        base=row('fixture','goods',10000,8000,order='base')
        a=dict(row('fixture','goods',10000,9000,order='a'),
               goods_risk_sku30=dict(arm='A',applied=True))
        untreated=dict(row('fixture','goods',10000,order='u'),
               goods_risk_sku30=dict(arm='B',applied=False))
        treated=dict(row('fixture','goods',10000,order='b'),
               goods_risk_sku30=dict(arm='B',applied=True))
        paid={'base':'confirmed','a':'confirmed'}
        expected=rp.stats_from_rows([base,a,untreated],paid,NOW)
        actual=rp.stats_from_rows([base,a,untreated,treated],paid,NOW)
        self.assertEqual(expected['pool']['goods']['n'],3)
        self.assertAlmostEqual(expected['pool']['goods']['p'],2/3)
        for field in ('pool','shops','nofit'):self.assertEqual(actual[field],expected[field])
        self.assertEqual(actual['counts']['excluded_rows'],expected['counts']['excluded_rows']+1)
        from test_buyer_shop_profile import NOW as profile_now
        rows=holder('H',[1.08]*20)
        pollution=[dict(r,goods_risk_sku30=dict(arm='B',applied=True)) for r in holder('H',[1.3]*100)]
        self.assertEqual(sp.profile_from_rows(rows,{},profile_now),{'H':108})
        self.assertEqual(sp.profile_from_rows(rows+pollution,{},profile_now),{'H':108})
        with tempfile.TemporaryDirectory() as md:
            p=Path(md)/'measure';p.mkdir()
            (p/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in [base,a,dict(untreated,state='TASK_STATE_COMPLETED',price=8000,passed_to_kit=True),
                    dict(treated,state='TASK_STATE_COMPLETED',price=8000,passed_to_kit=True)]))
            (Path(md)/'payments.jsonl').write_text(''.join(json.dumps(dict(order=k,paid=v))+'\n'
                  for k,v in [('base',True),('a',True),('u',False),('b',False)]))
            rates,prior=b1.paid_rates(md,now=NOW)
            self.assertAlmostEqual(prior,2/3)

    def test_new_selection_uses_original_main_ladder_wire_and_alternative_receipt_cap(self):
        import ast
        import market_buyer as mb
        import buyer
        from types import SimpleNamespace
        from test_buyer_reply import task
        tree=ast.parse(Path(mb.__file__).read_text())
        ladder=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='price_ladder')
        call=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call)
                  and isinstance(n.value.func,ast.Name) and n.value.func.id=='negotiate')
        self.flag()
        with patch.object(trial.time,'time',return_value=2000000001):
            self.reserve()
            offer=trial.dispatch(self.shops[0],12000,self.base)
        self.assertEqual(offer['sku'],'G22222222')
        text='fixture low (G22222222). Asking 4000. Includes a 6-hour warranty.'
        shop=self.shops[0];shop['_offer']=offer
        q=dict(ask=4000,ask_src='metadata',text=text,url='fixture',task=task(text,4000))
        limit=mb.saved_cap(offer,.09,share=.32)
        self.assertEqual(limit,5186)
        env=dict(mb.__dict__,md=self.md,order=self.order,offer=offer,q=q,steps=(.9,),
                 shop=shop,key=offer['sku'],label='fixture low',limit=limit,top=limit,note='',r3=True,
                 resume=('fixture',q['task']),probe_arm=None,discount_arm=None,goods_first_arm=None,
                 args=SimpleNamespace(budget=12000))
        exec(compile(ast.fix_missing_locations(ast.Module([ladder],type_ignores=[])),'<real-ladder>','exec'),env)
        with patch.object(buyer,'send',return_value=task('',state='TASK_STATE_COMPLETED')) as send, \
             patch.object(buyer,'rpc',return_value=None),patch.object(buyer,'log'), \
             patch.object(buyer,'decide',side_effect=AssertionError('code-priced, no model')):
            exec(compile(ast.fix_missing_locations(ast.Module([call],type_ignores=[])),'<real-main-wire>','exec'),env)
        self.assertEqual([c.args[1] for c in send.call_args_list],['3600'])
        self.assertIsNotNone(mb.over_limit({'item':'G22222222','price':5500},offer,limit,{},12000))
        self.assertIsNone(mb.warranty_short({'warranty_hours':6},dict(kind='goods')))
        self.assertIsNotNone(mb.warranty_short({'warranty_hours':0},dict(kind='goods')))

    def test_serialized_order_has_zero_off_delta_and_only_one_small_active_note(self):
        import market_buyer as mb
        from types import SimpleNamespace
        shop=dict(self.shops[0],_offer=self.high,_b=.15)
        def record(order):
            return mb.order_record(order,SimpleNamespace(budget=12000,item='goods'),
                 [shop],{},shop['name'],'choice',shop,'chosen',self.high,6000,.15,
                 dict(state='NO_VALUE',history=[]),None,dict(receipt=None),[],[],False,None,{}, {})
        with patch.object(mb,'buyer_cfg',return_value={}),patch.object(mb.time,'time',return_value=2000000001):
            before=record(self.order)
            self.assertIsNone(self.reserve())
            off=record(self.order)
            self.assertEqual(json.dumps(before,sort_keys=True),json.dumps(off,sort_keys=True))
            self.assertNotIn('goods_risk_sku30',off)
            self.flag();self.reserve()
            active=record(self.order)
        extra=len((json.dumps(active,ensure_ascii=False)+'\n').encode())-len((json.dumps(off,ensure_ascii=False)+'\n').encode())
        self.assertGreater(extra,0);self.assertLessEqual(extra,150)
        self.assertEqual(json.dumps(active).count('goods_risk_sku30'),1)
        self.assertTrue(all('goods_risk_sku30' not in q for q in active['quotes']))

    def test_atomic_seen_allows_one_concurrent_selection_without_waiting(self):
        import concurrent.futures
        self.flag()
        def invoke(i):
            order=copy.deepcopy(self.order);shops=copy.deepcopy(self.shops)
            return self.reserve(order=order,shops=shops)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(invoke,range(8)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(len(list(Path(self.md).glob(trial.PREFIX+'*.json'))),1)

    def test_oversize_integer_switch_fails_closed_without_crashing_main(self):
        self.flag(start=10**400,end=10**400+1800)
        self.assertIsNone(trial.config(self.md,2000000001))
        with patch.object(trial.time,'time',return_value=1999999990):
            with self.assertRaises(ValueError):
                trial.switch_on(self.md,study='fixture',salt='fixture',start=10**400,end=10**400+1800)

    def test_existing_price_trials_are_excluded_before_selection_but_not_read_off(self):
        self.assertIsNone(self.reserve(conflict=lambda: (_ for _ in ()).throw(AssertionError('OFF conflict lookup'))))
        self.flag()
        self.assertIsNone(self.reserve(conflict=lambda:True))
        self.assertIsNone(trial.measurement(self.order))
        self.assertEqual(len(list(Path(self.md).iterdir())),1)

    def test_existing_break_model_can_choose_lower_risk_acceptable_sku(self):
        high = dict(sku='G11111111', name='fixture high', list_price=10000, qty=1)
        low = dict(sku='G22222222', name='fixture low', list_price=8000, qty=1)
        unknown = dict(sku='G33333333', name='fixture unknown', list_price=7000, qty=1)
        offer = trial.candidate([high, unknown, low], high, 12000,
                                lambda sku: {'G11111111': .15, 'G22222222': .09}.get(sku))
        self.assertEqual(offer, low)
        self.assertEqual(high['sku'], 'G11111111')

    def test_finite_dispatch_locks_same_sku_for_final_talk_but_never_starts_after_end(self):
        with tempfile.TemporaryDirectory() as md:
            cfg = dict(enabled=True, study='risk-test', salt='risk-test', start=2000000000, end=2000001800)
            p = Path(md)/trial.SWITCH
            p.write_text(json.dumps(cfg)); os.utime(p, (1999999990,1999999990))
            high = dict(sku='G11111111', list_price=10000, qty=1)
            low = dict(sku='G22222222', list_price=8000, qty=1)
            shops = [dict(name='fixture',url='fixture',usable=[high,low])]
            oid = next(str(i) for i in range(100) if trial.arm(cfg,str(i))=='B')
            order = dict(kind='goods',order=oid,_buyer_started_at=2000000001)
            baseline = lambda offers,budget: max(offers,key=lambda o:o['list_price'])
            with patch.object(trial.time,'time',return_value=2000000001):
                trial.reserve(md,order,shops,12000,baseline,lambda sku: .15 if sku==high['sku'] else .09)
                self.assertEqual(trial.dispatch(shops[0],12000,baseline), low)
                self.assertTrue(trial.measurement(order)['applied'])
            with patch.object(trial.time,'time',return_value=cfg['end']):
                self.assertEqual(trial.offer(shops[0],12000,baseline), low, 'a sent quote must keep its SKU')
                fresh = dict(name='fixture',url='fixture',usable=[high,low])
                expired = dict(order,order='fresh')
                self.assertIsNone(trial.reserve(md,expired,[fresh],12000,baseline,lambda sku:.01))
                self.assertEqual(trial.dispatch(fresh,12000,baseline),high)

    def test_real_quote_dispatch_and_final_offer_use_same_alternative_sku(self):
        import market_buyer as mb
        high = dict(sku='G11111111', name='fixture high', list_price=10000, qty=1)
        low = dict(sku='G22222222', name='fixture low', list_price=8000, qty=1)
        with tempfile.TemporaryDirectory() as md:
            cfg = dict(enabled=True,study='risk-test',salt='risk-test',start=2000000000,end=2000001800)
            p = Path(md)/trial.SWITCH;p.write_text(json.dumps(cfg));os.utime(p,(1999999990,1999999990))
            oid=next(str(i) for i in range(100) if trial.arm(cfg,str(i))=='B')
            order=dict(kind='goods',order=oid,_buyer_started_at=2000000001)
            shop=dict(name='fixture',url='fixture',usable=[high,low])
            sent=[]
            def quote(s,label,sku,order,got,ctl):
                sent.append((label,sku))
            with patch.object(trial.time,'time',return_value=2000000001), patch.object(mb,'quote',quote):
                trial.reserve(md,order,[shop],12000,mb.pick_offer,lambda sku:.15 if sku==high['sku'] else .09)
                threads=mb.start_quotes([shop],order,12000,lambda sku:.09,{},mb.quote_ctl())
                for th in threads.values():th.join(1)
                self.assertEqual(sent,[('fixture low','G22222222')])
            with patch.object(trial.time,'time',return_value=cfg['end']):
                self.assertEqual(mb.selected_offer(shop,12000),low)
                self.assertEqual(shop['_offer'],low)

    def test_actual_b_price_and_quote_learning_are_excluded_but_a_and_unapplied_stay(self):
        import market_buyer as mb
        rows=[dict(order=str(i),kind='goods',ts=2000000001,
                   goods_risk_sku30=dict(study='risk-test',arm=a,applied=applied))
              for i,(a,applied) in enumerate([('B',True),('A',True),('B',False)])]
        self.assertEqual([r['order'] for r in mb.priced(rows)],['1','2'])
        with tempfile.TemporaryDirectory() as md:
            p=Path(md)/'measure';p.mkdir()
            (p/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            self.assertEqual([r['order'] for r in mb.recent_orders(md,2000000002,3)],['1','2'])
