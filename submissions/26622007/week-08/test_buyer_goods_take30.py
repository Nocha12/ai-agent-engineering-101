"""Finite cheap metadata goods take: allocation and final wire, never a market call."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import buyer
import buyer_goods_take30 as gt
from test_buyer_reply import task

class GoodsTake30Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        self.now = 2000000001
        self.cfg = dict(enabled=True, study='goods-take-test', salt='test', start=2000000000, end=2000001800)
        self.flag()
        self.order = dict(kind='goods', order='o0', _buyer_started_at=self.now)
        # Find B deterministically, no product arm override.
        self.order['order'] = next('o'+str(i) for i in range(100) if gt.arm(self.cfg, 'o'+str(i)) == 'B')
        self.offer = dict(sku='G12345678', name='fixture goods', list_price=10000)
        self.text = 'fixture goods (G12345678). Asking 4000. Includes a 6-hour warranty.'
        self.q = dict(ask=4000, ask_src='metadata', text=self.text, url='fixture',
                      task=task(self.text, 4000))
        self.shop = dict(name='fixture-shop')
    def flag(self, **changes):
        p = Path(self.md)/gt.SWITCH
        p.write_text(json.dumps(self.cfg | changes))
        os.utime(p, (1999999990, 1999999990))
    def assign(self):
        return gt.assign(self.md, self.order, now=self.now, env={})
    def run_wire(self, *, stopped=None, repeat=False, unknown=False, expire=False, **changes):
        self.assign()
        q = changes.pop('q', self.q)
        order = changes.pop('order', self.order)
        sent = []
        def send(url, value, tid=None):
            sent.append(value)
            if unknown: raise buyer.RpcReplyError({'method':'SendMessage'})
            return task(self.text, 4000) if repeat else task('', state='TASK_STATE_COMPLETED')
        def ladder(meta, text, history):
            return {'shop_price':meta, 'action':'offer' if not history else 'walk_away', 'price':3600}
        def guard(d, h):
            if expire: self.flag(enabled=False)
            return d
        with patch.object(gt.time, 'time', return_value=self.now), patch.object(buyer, 'send', send), \
             patch.object(buyer, 'rpc', return_value=None), patch.object(buyer, 'log'), \
             patch.object(buyer, 'decide', side_effect=AssertionError('no model')):
            out = gt.negotiate(buyer.negotiate, self.md, order, self.offer, q, (.9,), self.shop,
                'G12345678', 'fixture', 'fixture goods', changes.pop('limit', 6000), '',
                resume=('fixture',q['task']), ladder=ladder, take_at=None,
                stop_if=lambda text, turn: stopped, decision_guard=guard, **changes)
        return sent, out
    def test_B_exact_ask_reaches_real_send(self):
        sent, out = self.run_wire()
        self.assertEqual(sent, ['4000'])
        self.assertTrue(self.order['_goods_take30']['applied'])
        self.assertEqual(out['history'], [('shop asked',4000),('you offered',4000)])
    def test_expiry_at_final_guard_is_baseline_not_B(self):
        self.assertEqual(self.run_wire(expire=True)[0], ['3600'])
    def test_stop_if_has_precedence(self):
        self.assertEqual(self.run_wire(stopped='no warranty')[0], [])
    def test_unknown_is_never_retried(self):
        sent, out = self.run_wire(unknown=True)
        self.assertEqual(sent, ['4000'])
        self.assertEqual(out['state'], 'UNKNOWN_RPC_OUTCOME')
    def test_repeated_same_ask_not_retaken(self):
        self.assertEqual(self.run_wire(repeat=True)[0], ['4000'])
    def test_metadata_conflict_or_no_warranty_or_no_resume_is_A(self):
        for q in (dict(self.q, ask=4001), dict(self.q, ask_src='text'),
                  dict(self.q, text='G12345678 no warranty'), dict(self.q, closed=True),
                  dict(self.q, task=task(self.text, 4500))):
            with self.subTest(q=q):
                # Different invocation requires a fresh journal for each case.
                self.order.pop('_goods_take30',None)
                for p in Path(self.md).glob(gt.JOURNAL+'*'): p.unlink()
                self.assertEqual(self.run_wire(q=q)[0], ['3600'])
    def test_invalid_windows_and_money_fail_closed(self):
        for changes in (dict(start=None),dict(start=True),dict(end=float('nan')),
                        dict(end=float('inf')),dict(end=2000001801),dict(enabled=False)):
            self.flag(**changes)
            self.assertIsNone(gt.config(self.md, {}, self.now))
        self.flag()
        for now in (1999999999,2000001800,True,float('nan'),float('inf')):
            self.assertIsNone(gt.config(self.md, {}, now))
    def test_reused_order_and_journal_failure_fail_closed(self):
        self.assertIsNotNone(self.assign())
        other=dict(self.order)
        self.assertIsNone(gt.assign(self.md, other, now=self.now, env={}))
        self.assertEqual(other['_goods_take30']['fallback'], 'reused_order')
        self.order.pop('_goods_take30',None)
        for p in Path(self.md).glob(gt.JOURNAL+'*'): p.unlink()
        with patch.object(gt.os,'fsync',side_effect=OSError('fixture')):
            self.assertIsNone(self.assign())
        self.assertFalse(self.order['_goods_take30']['applied'])

    def test_A_OFF_end_and_positive_short_warranty(self):
        self.order['order']=next('a'+str(i) for i in range(100) if gt.arm(self.cfg,'a'+str(i))=='A')
        self.assertEqual(self.run_wire()[0], ['3600'])
        for p in Path(self.md).glob(gt.JOURNAL+'*'): p.unlink()
        self.flag(enabled=False)
        self.assertEqual(self.run_wire()[0], ['3600'])
        self.flag()
        self.order['order']=next('b'+str(i) for i in range(100) if gt.arm(self.cfg,'b'+str(i))=='B')
        self.order['_sw']={'enabled':True}
        self.text=self.text.replace('6-hour','3-hour')
        self.q.update(text=self.text,warranty_h=3.0,task=task(self.text,4000))
        self.assertEqual(self.run_wire()[0], ['4000'])

    def test_negative_bool_zero_overcap_noresume_and_task_or_sku_race(self):
        for a in (-1,True,0,5001,6001,float('nan'),float('inf'),4000.1):
            with self.subTest(ask=a):
                q=dict(self.q,ask=a)
                # Direct scope check cannot accidentally enter the bare TAKE branch.
                self.assertIsNotNone(gt.reason(self.order,self.offer,q,(.9,),self.shop,'G12345678',
                    6000,('fixture',q['task']),None))
        self.assertIsNotNone(gt.reason(self.order,self.offer,self.q,(.9,),self.shop,'G12345678',0,None,None))
        self.assertIsNotNone(gt.reason(self.order,self.offer,self.q,(.9,),self.shop,'G87654321',6000,
            ('fixture',self.q['task']),None))

    def test_intent_io_failure_and_mutated_allocation_are_A(self):
        self.assign()
        original=gt._journal
        def journal(md,cfg,event,oid,note=None):
            if event=='intent': raise OSError('fixture intent failure')
            return original(md,cfg,event,oid,note)
        with patch.object(gt,'_journal',journal):
            self.assertEqual(self.run_wire()[0], ['3600'])
        self.assertFalse(self.order['_goods_take30']['applied'])

    def test_real_main_negotiate_expression_reaches_wire_and_food_unchanged(self):
        import ast
        import market_buyer as mb
        from types import SimpleNamespace
        tree=ast.parse(Path(mb.__file__).read_text())
        assignment=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and
            isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and
            isinstance(n.value.func.value,ast.Name) and n.value.func.value.id=='buyer_goods_take30'
            and n.value.func.attr=='negotiate')
        self.assign()
        env=dict(mb.__dict__,md=self.md,order=self.order,offer=self.offer,q=self.q,steps=(.9,),
            shop=dict(name='fixture-shop',url='fixture'),key='G12345678',label='fixture goods',limit=6000,
            top=6000,note='',r3=True,resume=('fixture',self.q['task']),
            probe_arm=None,discount_arm=None,price_ladder=lambda m,t,h:
                dict(shop_price=m,action='offer',price=3600),
            request_text=lambda *args:'fixture request',quote_refused=lambda *args,**kw:None,
            goods_text_refused=lambda *args:None,shop_question_reply=lambda *args:None)
        with patch.object(gt.time,'time',return_value=self.now), patch.object(buyer,'send',
             return_value=task('',state='TASK_STATE_COMPLETED')) as send, patch.object(buyer,'log'):
            exec(compile(ast.fix_missing_locations(ast.Module([assignment],type_ignores=[])),
                 '<real-main-negotiate>', 'exec'),env)
            self.assertEqual(send.call_args.args[1], '4000')
        env['order']={'kind':'food'}
        def unchanged(*args,**kw):
            self.assertEqual(kw['take_at'],6000)
            return {'state':'food-unmodified'}
        env.update(negotiate=unchanged,FOOD_TAKE_ASK=True,FOOD_TAKE_SHARE=.65)
        exec(compile(ast.fix_missing_locations(ast.Module([assignment],type_ignores=[])),
             '<real-main-food>', 'exec'),env)
        self.assertEqual(env['out']['state'],'food-unmodified')

if __name__ == '__main__': unittest.main()
