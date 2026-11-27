"""Buy gates outside the owner's minimum list (대표님 11/25 02:25 list, 11/26 09:3x "병신같은 가드 넣지 말라고",
11/26 09:35 "라운드 종료시간은 고려하지마"); audit review/W-20261124-1650-AXRound4준비/purchase-gate-audit-1127.md D-1..D-5.
Allowed: class kit limits, food only, expected sale > cost + fees, 3 items a window / 4 each, same-quote re-confirm.
Market facts (supply window closed, 429, balance) are not gates.
Run from ops/: ~/ax-round4-venv/bin/python -m pytest -q test_buy_gates.py"""
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def goal():
    return json.loads((HERE.parent / 'agent.json').read_text(encoding='utf-8'))['goal']


class GoalHasNoStockOrEndGate(unittest.TestCase):
    """D-2, D-3: goal sentences that act as a total-stock cap or an end-time rule."""

    def test_no_stock_cap_sentences(self):
        g = goal()
        for s in ('재고만 쌓아 두거나', '유통기한 안에 팔 수 있는 양만 산다', '지금 가진 그 품목 재고를 뺀다',
                  '유통기한 안에 팔 수 있는 양까지'):
            self.assertNotIn(s, g)

    def test_no_end_time_rules(self):
        g = goal()
        for s in ('종료 24시간', '24시간 전부터', '6시간 전부터', '마지막 6시간', '종료 시각을 알게 되면',
                  '종료 시각은 /rules', '종료 시각이 공지되지 않았으면', "notes의 'R4 종료 시각' 줄"):
            self.assertNotIn(s, g)

    def test_food_is_not_six_words(self):
        self.assertNotIn('식품 필수품(생수, 우유, 쌀, 계란, 면, 과일)만 둔다', goal())

    def test_owner_minimum_list_kept(self):
        g = goal()
        for s in ('품목당 최대 4개', '한 창 최대 3품목', '후보는 식품뿐', '실제로 팔리는 값으로 팔아도 남지 않으면 사지 않는다',
                  '이미 가진 재고가 있어도 그것만으로 매입을 멈추지 않는다', '공급 창이 열리면 바로 산다'):
            self.assertIn(s, g)

    def test_stock_and_speed_are_reference_only(self):
        self.assertIn('판매 속도와 지금 가진 재고는 참고만 하고 매입을 멈추거나 줄이는 이유로 쓰지 않는다', goal())


if __name__ == '__main__':
    unittest.main()
