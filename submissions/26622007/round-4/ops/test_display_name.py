"""The shared display name block (대표님 11/27 01:26): the same text in forum_menu and the four tools, and its rules.
python3 -I ops/test_display_name.py                      the test
python3 -I ops/test_display_name.py --live SHOPS CATALOG  the rule check over the live stock (public /api/shops and
                                                          /api/catalog saved to files; no key is used), item by item"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = [os.path.join(HERE, 'forum_menu.py')] + [os.path.join(HERE, 'tools_src', t, 'logic.py')
                                                 for t in ('check_item_inventory', 'write_shop_card', 'refresh_full_card')]
FILES.append(os.path.join(HERE, 'restock_shoutout', 'logic.py'))   # its restock posts name items too
START, END = '# --- shared display name block', '# --- end shared display name block'


def block(path):
    s = open(path, encoding='utf-8').read()
    assert s.count(START) == 1 and s.count(END) == 1, path
    return s[s.index(START):s.index(END)]


def load():
    texts = [block(p) for p in FILES]
    assert len(set(texts)) == 1, 'the display name block differs between files'
    ns = {'re': re}
    exec(texts[0], ns)
    return ns


def main():
    ns = load()
    for f in FILES:                                      # no place cuts a name by characters any more
        s = open(f, encoding='utf-8').read()
        assert not re.search(r"(?<!args)\.get\('name'\)[^\n]*\.strip\(\)\[:\d+\]", s), f
        assert 'def short(' not in s, f
    dn, names, check = ns['display_name'], ns['display_names'], ns['display_check']
    assert dn('[농협] 안동생명콩 국산콩 연두부 300g×5팩') == '연두부300g×5팩'
    assert dn('정식수입 사토노유키 실큰두부 300g 1팩 상온보관 멸균두부 연두부 순두부 단백질 식단관리') == '실큰두부300g×1팩'
    assert dn('한계란 난각번호 1번 계란 무항생제 유정란 20구') == '계란20구'
    assert dn('[본사공식] 몽베스트 무라벨 생수 2Lx6병(1팩)') == '생수2L×6병,1팩'
    assert dn('스테비아 대추방울토마토 레드 스윗마토 1.5kg (500gx3팩)') == '방울토마토1.5kg,500g×3팩'
    assert dn('바로먹는 직화 을지로 노가리 28마리 내외 (50g x 2봉)') == '노가리28마리,50g×2봉'
    assert dn('핸드드립커피 드립백커피 6개국 모듬 8g x 36개입') == '커피8g×36개입'     # 6개국 is not a count
    assert dn('소화가잘되는우유 오리지널 190ML 24팩') == '우유190ml×24팩'
    assert dn('[브랜드] 처음보는물건이름길다아주길다 1개') == '처음보는물건이름1개'     # only the word is shortened
    # review r1 I4: a size followed by Hangul, a digit or SET is still a size, and the check reads names on its own
    assert dn('손질 가자미 특대 4~6미 1.4kg내외') == '가자미4~6미,1.4kg'
    assert dn('국내산 양배추 5kg이상') == '양배추5kg'
    assert dn('세일즈카페 케냐AA 200g원두커피') == '커피200g'
    assert dn('생수2L6병') == '생수2L×6병'
    assert dn('[본죽] 파우치죽 200g 4종 5팩SET(전복2+쇠고기+삼계+낙지김치)') == '파우치죽200g,5팩'
    assert dn('HACCP인증 무항생제 농장직송 달걀 생계란 대란 30구 2판') == '계란30구×2판'    # 60 eggs, not 30 (r1 M3)
    assert dn('[호랑이김치] 국산100% 토굴숙성 멸치액젓 1kg') == '액젓1kg'           # no digits run into the size
    assert check({'a': '국내산 양배추 5kg이상'}, {'a': '양배추'}) == {'a': ['용량·묶음 빠짐 5kg']}
    assert check({'a': '손질 가자미 1.4kg내외'}, {'a': '가자미'}) == {'a': ['용량·묶음 빠짐 1.4kg']}
    # r1 M2: a word that only contains a kind word is another thing
    assert dn('두유과자 100g') == '두유과자100g' and dn('순두부찌개 양념 500g') == '순두부찌개500g'
    # r1 M1: a renamed item keeps its whole product word
    two = {'A': '한상궁김치 총각김치 2kg/단단한 알타리', 'B': '맛있는 총각김치 2kg'}
    assert names(two) == {'A': '한상궁김치총각김치2kg', 'B': '맛있는총각김치2kg'}, names(two)
    two = {'A': '동원샘물 2L x 6병 (무라벨)', 'B': '동원샘물 2L x 6병 (무라벨/생수)'}
    assert len(set(names(two).values())) == 2, names(two)
    shop = {'R1': '2026년 햅쌀 씻어나온쌀 5kg 상등급', 'R2': '2026년 햅쌀 국산 백미 쌀 5kg 상등급',
            'R3': '26년산 새청무 특등급 단일품종 20kg 쌀 백미', 'R4': '[안동농협] 26년 햅쌀 밥이 다르다 백진주쌀 20kg',
            'T1': '[농협] 안동생명콩 국산콩 연두부 300g×5팩',
            'T2': '정식수입 사토노유키 실큰두부 300g 1팩 상온보관 멸균두부 연두부 순두부 단백질 식단관리'}
    shown = names(shop)
    assert shown == {'R1': '씻어나온쌀5kg', 'R2': '백미쌀5kg', 'R3': '새청무쌀20kg', 'R4': '백진주쌀20kg',
                     'T1': '연두부300g×5팩', 'T2': '실큰두부300g×1팩'}, shown
    assert check(shop, shown) == {}
    # each rule catches its own break
    bad = check(shop, dict(shown, R1='쌀5kg', R2='쌀5kg', T1='연두부300g', T2='두부300g×1팩'))
    assert bad == {'R1': ['같은 표시 쌀5kg'], 'R2': ['같은 표시 쌀5kg'], 'T1': ['용량·묶음 빠짐 5팩'],
                   'T2': ['종류어 빠짐 실큰두부']}, bad
    eggs = {'E1': 'Fresh 한끼해줘 1+등급란 40구 특란', 'E2': '품질보장 난각번호 1번 무항생제 유정란 40구',
            'E3': '한계란 무항생제 동물복지 유정란 난각번호 2번 계란 40구', 'E4': '온밀담 난각번호 2번 유정란 40구 동물복지/ GS단독',
            'E5': '한계란 난각번호 1번 계란 무항생제 유정란 40구'}    # the whole market 11/27: no one word tells E3 and E5 apart
    shown = names(eggs)
    assert check(eggs, shown) == {} and shown['E3'] == '한계란동물복지40구' and shown['E5'] == '한계란1번40구', shown
    same = {'Gaaaa1111': '아주 긴 이름 500g', 'Gbbbb2222': '아주 긴 이름 500g'}   # nothing in the name: the SKU tail (r2 I5)
    shown = names(same)
    assert shown == {'Gaaaa1111': '아주500g·1111', 'Gbbbb2222': '아주500g·2222'}, shown
    assert check(same, shown) == {}
    # step-2 review r2
    assert dn('국산 설탕 1,000g') == '설탕1000g' and dn('설탕 1,000g x 2') == '설탕1000g×2'          # I2
    assert dn('다크&밀크더블코팅 원데이그래놀라 초코바 10개세트X3') == '그래놀라10개×3'             # I3: 30 bars
    assert dn('국내산 사과 1kgx3') == '사과1kg×3' and dn('라면x20봉') == '라면20봉'
    assert dn('[모던하우스] 과일야채컵 650ml블루') == '야채컵650ml'                              # I4 (Hangul after)
    assert dn('크라운 밤양갱 팥양갱 400g(50gx8개입)') == '양갱400g,50g×8개입'                   # I7
    assert dn('나주 배 5kg') == '배5kg' and dn('박수홍의 착한 뼈없는 갈비탕 750g X 10봉') == '갈비탕750g×10봉'
    assert dn('[조선호텔] 블랙 초코 케이크 800g') == '초코케이크800g'
    assert dn('티젠 콤부차 10스틱') == '콤부차'                                                  # 10스틱 is no word
    assert dn('동원 살코기 135gX2+동원 마일드150gX2총4캔') == '동원135g×2, 150g×2, 4캔'   # never 2,150; each ×2 (r3 I3)
    assert dn('3구성 세트 과자') == '과자' and dn('노가리 28마리') == '노가리28마리'                # M2, M3
    assert dn('우유 1ℓ') == '우유1L' and dn('우유 200㎖') == '우유200ml'                         # M3 (NFKC)
    assert dn(None) == '' and dn('12개입') == '12개입' and names({'a': None}) == {'a': ''}       # M4: no ?, no None?
    assert len(dn('라면 ' * 3000)) < 40
    tags = {'A1': '계란 20구 (A)', 'B1': '계란 20구 (B)'}                                          # I5: one-letter tags
    assert names(tags) == {'A1': 'A계란20구', 'B1': 'B계란20구'}, names(tags)
    cut = {'X1': '가나다라마바사아자 계란 20구', 'X2': '가나다라마바사아차 계란 20구'}            # I5: never cut the word
    assert len(set(names(cut).values())) == 2, names(cut)
    dup = {'S1': '계란 20구', 'S2': '계란20구'}                                                    # M1: no size in the word
    assert all('20구20구' not in v for v in names(dup).values()) and len(set(names(dup).values())) == 2, names(dup)
    # the check reads the name on its own (r2 I1): it catches what a shown name lost, whatever made it
    assert check({'a': '설탕 1,000g'}, {'a': '설탕0g'}) == {'a': ['용량·묶음 빠짐 1000g']}
    assert check({'a': '노가리 28마리 (50g x 2봉)'}, {'a': '노가리50g×2봉'}) == {'a': ['용량·묶음 빠짐 28마리']}
    assert check({'a': '초코바 10개세트X3'}, {'a': '초코바10개'}) == {'a': ['용량·묶음 빠짐 ×3']}
    assert check({'a': '크라운 밤양갱 400g'}, {'a': '크라운400g'}) == {'a': ['종류어 빠짐 양갱']}
    assert check({'a': '연세우유 검은콩 두유 190ml'}, {'a': '두유190ml'}) == {}
    assert check({'a': '6개국 커피 8g'}, {'a': '커피8g'}) == {} and check({'a': '2,3과 사과'}, {'a': '사과'}) == {}
    # step-2 review r3
    import time as _t
    t0 = _t.time()
    dn(' ' * 2000), names({'a': ' ' * 2000, 'b': ' ' * 2000}), dn(' x' * 1000 + '1' * 1000)
    assert _t.time() - t0 < 0.5, 'a run of spaces is slow (r3 I1)'
    for n in ('11/27 입고 우유', '3 병아리콩', '맥반석 9 구운계란', '우유 2 팩트체크', '오징어 2 미니'):   # r3 I4
        assert not re.search(r'\d', dn(n)), (n, dn(n))
    assert dn('2024 매실청 1L') == '매실청1L' and dn('생수 2L 6 병') == '생수2L×6병' and dn('500g 2 팩') == '500g×2팩'
    assert check({'a': '3 병아리콩'}, {'a': '병아리콩'}) == {}
    assert dn('1kgx3 사과') == '사과1kg×3'                                                        # r3 M7
    tail = names({'Gaaaa1111': '아주 긴 이름 500g', 'Gbbbb1111': '아주 긴 이름 500g'})          # r3 M6
    assert len(set(tail.values())) == 2 and check({'Gaaaa1111': 'x', 'Gbbbb1111': 'x'}, tail) == {}, tail
    assert len(names({1: '계란 20구', 'G2': '계란 20구'})) == 2                                    # mixed key types
    assert check({'a': '[농협] 연두부 300g 순두부'}, {'a': '연두부300g'}) == {}                   # r3 M8
    assert check({'a': '두부 300g 2입 국산콩 두유'}, {'a': '두유300g×2입'}) == {'a': ['종류어 빠짐 두부']}
    units = [{'sku': 'A', 'name': '계란 20구', 'expires_at': 100}, {'sku': 'B', 'name': '계란 20구', 'expires_at': 300},
             {'sku': 'C', 'name': '계란 20구'}]
    assert ns['shop_names'](units, 200) == {'A': '계란20구·A', 'B': '계란20구·B', 'C': '계란20구·C'}
    units = [{'sku': 'Z', 'name': '계란 20구', 'expires_at': 0}, {'sku': 'O', 'name': '계란 20구', 'expires_at': 100},
             {'sku': 'L', 'name': '계란 20구 대란', 'expires_at': 300}]
    assert ns['shop_names'](units, 200) == {'Z': '계란20구', 'L': '대란계란20구', 'O': '계란20구·O'}, ns['shop_names'](units, 200)
    # step-2 review r4
    for n, want in (('생수 2 L x 6', '생수2L×6'), ('두부 300 g x 3', '두부300g×3'), ('라면 120g x5 x2', '라면120g×5×2'),   # I3
                    ('사과 1kg x3 (1kg x3)', '사과1kg×3'), ('라면 120gx5+120gx5', '라면120g×5'),                          # I4
                    ('4-6 미 가자미', '가자미4~6미'), ('두부 2 팩세트', '두부2팩')):                                      # I5, M6
        assert dn(n) == want and check({'a': n}, {'a': want}) == {}, (n, dn(n))
    assert check({'a': '가자미 4~6미'}, {'a': '가자미6미'}) == {'a': ['용량·묶음 빠짐 4~6미']}
    assert check({'a': '생수 2 L x 6'}, {'a': '생수2L'}) == {'a': ['용량·묶음 빠짐 ×6']}
    assert len(set(names({1: 'x 500g', '1': 'x 500g'}).values())) == 2                            # M9
    # step-2 review r5
    two = [{'sku': 'G0000cccc', 'name': '계란 20구 대란', 'expires_at': 0}, {'sku': 'GOLD1234', 'name': '계란 20구', 'expires_at': 100}]
    got = ns['shop_names'](two, 200)                                                               # I1: never alike
    assert len(set(got.values())) == 2 and got['G0000cccc'] == '계란20구', got
    got = ns['shop_names']([{'sku': 1, 'name': 'x 1g', 'expires_at': 100}, {'sku': '2', 'name': 'x 1g'}], 200)   # M1
    assert len(set(got.values())) == 2, got
    for n, want in (('삼겹살 500g x2 + 목살 500g x3', '삼겹살500g×2, 500g×3'),                    # I2: each count kept
                    ('라면 120g x5 + 120g x4', '라면120g×5, 120g×4'), ('감자 1kg 1kg x2', '감자1kg,1kg×2')):
        assert dn(n) == want and check({'a': n}, {'a': want}) == {}, (n, dn(n))
    t0 = _t.time()                                                                                 # M3: the count chain
    dn('1g' + 'x1' * 1000), dn('1gx1x1 ' * 280), names({'a': '1+' * 1000 + '가', 'b': '1+' * 1000 + '나'})
    assert _t.time() - t0 < 1, 'the count chain is slow'
    return 'ok'


def live(shops_path, catalog_path, name='nocha12'):
    ns = load()
    shops = json.load(open(shops_path, encoding='utf-8'))
    stock = next(s for s in shops['shops'] if s.get('name') == name)['stock']
    cat = {c['sku']: c for c in json.load(open(catalog_path, encoding='utf-8')) if isinstance(c, dict)}
    units = [{'sku': k, 'name': cat.get(k, {}).get('name') or k} for k in stock]
    shown, bad = ns['shop_check'](units)
    for u in sorted(units, key=lambda u: u['sku']):
        k = u['sku']
        print(k, stock[k], 'FAIL ' + '; '.join(bad[k]) if k in bad else 'ok', '|', shown[k], '|', u['name'])
    print(f'SKU {len(units)}  통과 {len(units) - len(bad)}  실패 {len(bad)}  (이름 없는 SKU {sum(1 for k in stock if k not in cat)})')
    return 0 if not bad else 1


if __name__ == '__main__':
    if sys.argv[1:2] == ['--live']:
        sys.exit(live(sys.argv[2], sys.argv[3]))
    print(main())
