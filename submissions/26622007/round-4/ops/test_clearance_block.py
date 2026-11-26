"""The shared clearance block (떨이, 대표님 11/26 19:05) is the same text everywhere it is used, and its numbers.
python3 -I ops/test_clearance_block.py"""
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = [os.path.join(HERE, 'tools_src', t, 'logic.py')
         for t in ('private_checkout', 'check_item_inventory', 'write_shop_card', 'refresh_full_card')] + [os.path.join(HERE, 'forum_menu.py')]
START, END = '# --- shared clearance block', '# --- end shared clearance block'


def block(path):
    s = open(path, encoding='utf-8').read()
    return s[s.index(START):s.index(END)]


def main():
    texts = [block(p) for p in FILES]
    assert len(set(texts)) == 1, 'the clearance block differs between files'
    ns = {'math': math}
    exec(texts[0], ns)
    T = 1795650000.0
    egg = {'kind': 'food', 'category': '식품>계란/유제품>계란', 'list_price': 12900, 'cost': 5000, 'sku': 'G1', 'name': '계란'}
    c = ns['clearance']
    assert c(dict(egg, expires_at=T + 25 * 3600), None, T) is None                    # 25 / 48 > 0.5
    a = c(dict(egg, expires_at=T + 18 * 3600), 7000, T)                               # r 0.375: 7000 x 0.875, no loss
    assert a == {'price': 6120, 'floor': 5470, 'hours_left': 18.0, 'r': 0.375, 'loss': False}, a
    b = c(dict(egg, expires_at=T + 9 * 3600), 7000, T)                                # r 0.1875: under cost allowed
    assert b['floor'] == math.ceil((5000 * 0.75 + 415) / 0.99) and b['price'] == 4810 and b['loss'], b
    z = c(dict(egg, expires_at=T + 60), None, T)                                      # the last minute: about 420
    assert z['floor'] == 427 and z['price'] == 3870, z
    assert c(dict(egg, name='몽베스트 생수 2L'), None, T) is None and c(dict(egg, sku='G00b8acf0', expires_at=T + 60), 1, T) is None
    assert c(dict(egg, kind='goods', expires_at=T + 60), None, T) is None
    assert c(dict(egg, expires_at=T - 1), None, T) is None and c(dict(egg, expires_at=True), None, T) is None
    long = dict(egg, category='식품>음료>커피/차', expires_at=T + 100 * 3600)            # 720 h item, 100 h left: 48 h cap
    assert c(long, None, T) is None and c(dict(long, expires_at=T + 12 * 3600), None, T)['r'] == 0.25
    odd = dict(egg, category='?', supplied_at=T - 10 * 3600, expires_at=T + 2 * 3600)  # unknown category: the span we had
    assert c(odd, None, T)['r'] == round(2 / 12, 3)
    assert c(dict(egg, category='?', expires_at=T + 3600), None, T) is None            # no span either: not on clearance
    for bad in ({'cost': '1,000'}, {'cost': None}, {'list_price': 'x'}, {'cert': 'x'}, {'category': None}):
        c(dict(egg, expires_at=T + 3600, **bad), None, T)                              # never raises
    print('ok')


if __name__ == '__main__':
    main()
