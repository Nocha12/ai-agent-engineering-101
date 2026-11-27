"""The shared quote block: one shown price per SKU and grade (operator 11/26 12:40, item 2).
python3 -I ops/test_quote_block.py"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = [os.path.join(HERE, 'tools_src', 'check_item_inventory', 'logic.py'),
         os.path.join(HERE, 'tools_src', 'private_checkout', 'logic.py'),
         os.path.join(HERE, 'restock_shoutout', 'logic.py')]
START, END = '# --- shared quote block', '# --- end shared quote block'


def block(path):
    s = open(path, encoding='utf-8').read()
    return s[s.index(START):s.index(END)]


def main():
    texts = [block(p) for p in FILES]
    assert len(set(texts)) == 1, 'the quote block differs between tools'
    ns = {'json': json, 'os': os}
    exec(texts[0], ns)
    T = 1795660000.0
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'quotes.json')
        assert ns['quotes_load'](p) == {}
        floors = {'C': 4000, 'B': 6000}
        assert ns['quote_prices']({}, 'G1', floors, T) == {'C': None, 'B': None}
        assert ns['quote_set'](p, 'G1', {'C': 9000}, floors, 'test', T) == {'C': 9000}
        assert ns['quote_set'](p, 'G1', {'C': 5000}, floors, 'test', T + 1) == {'C': 9000}      # first one wins
        assert ns['quote_set'](p, 'G1', {'B': 8900}, floors, 'test', T + 2) == {'B': 9000}      # B never under C
        q = ns['quotes_load'](p)
        assert ns['quote_prices'](q, 'G1', floors, T + 3) == {'C': 9000, 'B': 9000}
        assert ns['quote_set'](p, 'G1', {'C': 5000}, floors, 'test', T + 4, replace=True) == {'C': 5000}
        q = ns['quotes_load'](p)
        assert ns['quote_prices'](q, 'G1', floors, T + 5) == {'C': 5000, 'B': 9000}
        assert ns['quote_set'](p, 'G1', {'C': 9900}, floors, 'test', T + 6, replace=True) == {'C': 9000}  # not over B
        up = {'C': 12541, 'B': 6000}                    # dearer C units since: the stored C 9,000 is shown at its floor
        assert ns['quote_set'](p, 'G1', {'C': 9500}, up, 'test', T + 7) == {'C': 12541}         # review r1 I5
        assert ns['quote_prices'](ns['quotes_load'](p), 'G1', up, T + 7) == {'C': 12541, 'B': 12541}
        assert ns['quote_set'](p, 'G1', {'C': 9900}, {'C': 9500, 'B': 6000}, 't', T + 7, replace=True) == {'C': 9500}
        q = ns['quotes_load'](p)
        assert ns['quote_prices'](q, 'G1', {'C': 9500, 'B': 6000}, T + 8) == {'C': 9500, 'B': 9500}   # shown in order
        assert ns['quote_prices'](q, 'G1', {'C': 9800, 'B': 6000}, T + 8) == {'C': 9800, 'B': 9800}   # floor rose
        assert ns['quote_prices'](q, 'G1', floors, T + 24 * 3600 + 10) == {'C': None, 'B': None}      # a day old: gone
        ns['quote_set'](p, 'G2', {'A': 7000}, {'A': 3000}, 't', T + 24 * 3600 + 11)
        assert set(ns['quotes_load'](p)) == {'G2|A'}                                                  # old rows pruned
        open(p, 'w').write('not json')
        assert ns['quotes_load'](p) == {}
        assert ns['quote_set'](p, 'G3', {'B': 100}, {'B': 500}, 't', T) == {'B': 500}                # never under floor
        # cash-out (11/27): only a fresh quote stored by 'cashout' carries a floor under the goal (3) floor
        cq = {'G4|C': {'price': 11790, 'ts': T, 'by': 'cashout', 'floor': 11790},
              'G4|B': {'price': 11790, 'ts': T, 'by': 'forum_menu', 'floor': 11790},
              'G5|C': {'price': 300, 'ts': T, 'by': 'cashout', 'floor': 300},
              'G6|C': {'price': 5000, 'ts': T, 'by': 'cashout', 'floor': '5000'}}
        assert ns['quote_floor'](cq, 'G4', 'C', T) == 11790 and ns['quote_floor'](cq, 'G4', 'B', T) is None
        assert ns['quote_prices'](cq, 'G4', {'C': 12778, 'B': 12778}, T) == {'C': 11790, 'B': 12778}
        assert ns['quote_floor'](cq, 'G5', 'C', T) is None                                            # under 420: no
        assert ns['quote_prices'](cq, 'G5', {'C': 900}, T) == {'C': 900}
        assert ns['quote_floor'](cq, 'G6', 'C', T) is None                                            # odd floor: no
        assert ns['quote_floor'](cq, 'G4', 'C', T + 24 * 3600 + 1) is None                            # a day old
        assert ns['quote_prices'](cq, 'G4', {'C': 10000}, T) == {'C': 11790}                          # floor lower: price
        ns['quote_set'](p, 'G7', {'C': 9000}, {'C': 9500}, 't', T)                                    # quote_set never
        assert ns['quote_floor'](ns['quotes_load'](p), 'G7', 'C', T) is None                          # writes cashout
    print('quote block ok')


if __name__ == '__main__':
    main()
