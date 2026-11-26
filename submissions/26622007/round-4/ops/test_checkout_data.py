"""11/27 06:40 check: every attached checkout also goes out whole as an A2A data part (customer text cut at 2,000)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkout_delivery as cd

import base64


def tok(**pl):
    body = base64.urlsafe_b64encode(json.dumps(dict(pl, cert={'pad': 'x' * 500})).encode()).decode().rstrip('=')
    return 'eyJhbGciOiJFSVAxOTEiLCJ0eXAiOiJKV1QifQ.' + body + '.' + 'b' * 130


T1 = tok(id='checkout', item='G159be1f8', price=2634, claim='C', unit='u-c654df1133b3', task='t')
T2 = tok(id='checkout', item='G29055d88', price=6750, claim='B', unit='u-0000aaaa1111', task='t')
F1 = {'item': 'G159be1f8', 'price': 2634, 'claim': 'C', 'unit': 'u-c654df1133b3'}
F2 = {'item': 'G29055d88', 'price': 6750, 'claim': 'B', 'unit': 'u-0000aaaa1111'}
NOW = 1_795_700_000.0


def customer_view(result):
    """house/customer.py talk(): what the customer tool keeps of our reply."""
    said = ' '.join(p.get('text', '') for p in result['status']['message']['parts'])
    data = [p['data'] for a in result.get('artifacts') or [] for p in a.get('parts', []) if 'data' in p]
    return said[:2000], data[:3]


class AttachData(unittest.TestCase):
    def result(self, text):
        return {'id': 't', 'status': {'state': 'TASK_STATE_INPUT_REQUIRED',
                                      'message': {'messageId': 'm', 'parts': [{'text': text}]}}}

    def test_two_checkouts_both_reach_the_customer_whole(self):
        note = '등급 근거 ' + 'x' * 540
        issued = [cd.Checkout('o1', T1, NOW, note), cd.Checkout('o2', T2, NOW, note)]
        body, attached = cd.compose('두 가지 결제서입니다.', issued, set(), NOW + 1)
        r = self.result(body)
        self.assertEqual(cd.attach_data(r, attached), 2)
        said, data = customer_view(json.loads(json.dumps(r)))
        self.assertNotIn(T2, said)                       # the text cut hides the second token (the old loss) ...
        self.assertEqual(data, [dict(F1, checkout_jwt=T1, order='o1'), dict(F2, checkout_jwt=T2, order='o2')])  # ... both
        self.assertEqual(r['status']['message']['parts'][0]['text'], body)   # text unchanged

    def test_existing_artifacts_kept(self):
        r = self.result('안녕하세요')
        r['artifacts'] = [{'artifactId': 'a', 'parts': [{'text': 'x'}]}]
        self.assertEqual(cd.attach_data(r, (cd.Checkout('o1', T1, NOW),)), 1)
        self.assertEqual(r['artifacts'][0]['artifactId'], 'a')
        self.assertEqual(r['artifacts'][1]['parts'], [{'data': dict(F1, checkout_jwt=T1, order='o1')}])
        self.assertEqual(cd.attach_data(None, (cd.Checkout('o1', T1, NOW),)), 0)
        r = self.result('x')
        r['artifacts'] = None
        cd.attach_data(r, (cd.Checkout('o1', T1, NOW),))
        self.assertEqual(len(r['artifacts']), 1)

    def test_all_cancelled_replaces_the_customers_last_data(self):
        """Review I2: a checkout reply attaching none still sends data without any token, so buy() with no token
        no longer finds the earlier one (house/customer.py keeps last_data until a reply carries data)."""
        issued = [cd.Checkout('o1', T1, NOW)]
        body, attached = cd.compose('취소했습니다', issued, {'o1'}, NOW + 1)
        self.assertEqual(attached, ())
        r = self.result(body)
        self.assertEqual(cd.attach_data(r, attached, {'o1'}), 0)
        _, data = customer_view(r)
        self.assertEqual(data, [{'checkouts': [], 'cancelled': ['o1']}])
        self.assertFalse(any('checkout_jwt' in d for d in data))

    def test_cancelled_one_of_two_is_left_out(self):
        issued = [cd.Checkout('o1', T1, NOW), cd.Checkout('o2', T2, NOW)]
        body, attached = cd.compose('하나만', issued, {'o1'}, NOW + 1)
        r = self.result(body)
        cd.attach_data(r, attached, {'o1'})
        self.assertEqual([p['data']['order'] for p in r['artifacts'][0]['parts']], ['o2'])

    def test_v03_parts_name_their_kind(self):
        r = dict(self.result('x'), kind='task')
        cd.attach_data(r, (cd.Checkout('o1', T1, NOW),))
        self.assertEqual(r['artifacts'][0]['parts'][0]['kind'], 'data')
        r = self.result('x')
        cd.attach_data(r, (cd.Checkout('o1', T1, NOW),))
        self.assertNotIn('kind', r['artifacts'][0]['parts'][0])

    def test_unreadable_payload_still_attaches_token(self):
        r = self.result('x')
        cd.attach_data(r, (cd.Checkout('o1', 'eyJa.!!.c', NOW),))
        self.assertEqual(r['artifacts'][0]['parts'][0]['data'], {'checkout_jwt': 'eyJa.!!.c', 'order': 'o1'})

    def test_sdk_parses_it(self):
        try:
            from google.protobuf.json_format import ParseDict
            from a2a.types import SendMessageResponse
        except Exception:
            self.skipTest('a2a sdk not here (market venv only)')
        r = dict(self.result('x'), contextId='c')
        r['status']['message']['role'] = 'ROLE_AGENT'
        cd.attach_data(r, (cd.Checkout('o1', T1, NOW), cd.Checkout('o2', T2, NOW)))
        m = ParseDict({'task': json.loads(json.dumps(r))}, SendMessageResponse())
        self.assertEqual(len(m.task.artifacts[0].parts), 2)
        r = self.result('x')
        cd.attach_data(r, (), {'o1'})
        ParseDict({'task': dict(r, contextId='c')}, SendMessageResponse())

    def test_only_attached_ones(self):
        issued = [cd.Checkout('o1', T1, NOW), cd.Checkout('o2', T2, NOW - 400)]   # o2 past its hold: not attached
        body, attached = cd.compose('결제서', issued, set(), NOW + 1)
        r = self.result(body)
        cd.attach_data(r, attached)
        self.assertEqual([p['data']['order'] for p in r['artifacts'][0]['parts']], ['o1'])


if __name__ == '__main__':
    unittest.main()
