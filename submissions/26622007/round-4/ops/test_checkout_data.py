"""11/27 06:40 check: every attached checkout also goes out whole as an A2A data part (customer text cut at 2,000)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkout_delivery as cd

T1 = 'eyJhbGciOiJFSVAxOTEiLCJ0eXAiOiJKV1QifQ.' + 'a' * 700 + '.' + 'b' * 130
T2 = 'eyJhbGciOiJFSVAxOTEiLCJ0eXAiOiJKV1QifQ.' + 'c' * 700 + '.' + 'd' * 130
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
        self.assertEqual(data, [{'checkout_jwt': T1, 'order': 'o1'}, {'checkout_jwt': T2, 'order': 'o2'}])  # ... data has both
        self.assertEqual(r['status']['message']['parts'][0]['text'], body)   # text unchanged

    def test_existing_artifacts_kept_and_nothing_when_no_checkout(self):
        r = self.result('안녕하세요')
        r['artifacts'] = [{'artifactId': 'a', 'parts': [{'text': 'x'}]}]
        self.assertEqual(cd.attach_data(r, ()), 0)
        self.assertEqual(len(r['artifacts']), 1)
        self.assertEqual(cd.attach_data(r, (cd.Checkout('o1', T1, NOW),)), 1)
        self.assertEqual(r['artifacts'][0]['artifactId'], 'a')
        self.assertEqual(r['artifacts'][1]['parts'], [{'data': {'checkout_jwt': T1, 'order': 'o1'}}])
        self.assertEqual(cd.attach_data(None, (cd.Checkout('o1', T1, NOW),)), 0)

    def test_only_attached_ones(self):
        issued = [cd.Checkout('o1', T1, NOW), cd.Checkout('o2', T2, NOW - 400)]   # o2 past its hold: not attached
        body, attached = cd.compose('결제서', issued, set(), NOW + 1)
        r = self.result(body)
        cd.attach_data(r, attached)
        self.assertEqual([p['data']['order'] for p in r['artifacts'][0]['parts']], ['o1'])


if __name__ == '__main__':
    unittest.main()
