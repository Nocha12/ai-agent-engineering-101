import json, re, time

FIELDS = ['호칭', '구매·흥정 이력', '선호 품목', '가격 민감도', '개인 맥락', '불만·환불']
FIXED = ['사는 동네', '최근 산 품목', '오늘 정보', '확인 시각']

def _path(kit):
    return 'customers.test.json' if getattr(kit, 'testing', False) else 'customers.json'

def _load(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

def _save(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

def _key(customer):
    raw = str(customer or '')
    if 'a person in your app' in raw.lower() or 'did not say who it is' in raw.lower():
        return None
    match = re.search(r"says it is\s+(['\"])(.*?)\1", raw, flags=re.I)
    value = match.group(2) if match else raw
    value = value.strip().strip(" \t\r\n'\"[]")[:40]
    return value or None

def _new(now):
    return {'호칭':'', '구매·흥정 이력':[], '선호 품목':[], '가격 민감도':'mid', '개인 맥락':[], '불만·환불':[], '방문 횟수':1, '마지막 방문 시각':now, '고정 정보':{'사는 동네':'', '최근 산 품목':[], '오늘 정보':'', '확인 시각':0}}

def _summary(rec, is_new=False):
    out = {'new': True} if is_new else {
        '호칭': rec.get('호칭',''),
        '방문 횟수': rec.get('방문 횟수',1),
        '마지막 방문': rec.get('마지막 방문 시각'),
        '최근 구매 2건': rec.get('구매·흥정 이력',[])[-2:],
        '선호 품목 3개': rec.get('선호 품목',[])[:3],
        '가격 민감도': rec.get('가격 민감도','mid'),
        '개인 맥락 키워드 2개': rec.get('개인 맥락',[])[-2:]
    }
    fixed = rec.get('고정 정보') or {}
    out['고정 정보'] = {name: fixed.get(name, [] if name == '최근 산 품목' else (0 if name == '확인 시각' else '')) for name in FIXED}
    return out

def _say_only(value):
    s = str(value)
    return s if s.startswith('손님 말:') else '손님 말:' + s

def _empty(value, name):
    if value is None or value is False or value == '':
        return True
    if name == '최근 산 품목' and (value == [] or (isinstance(value, dict) and value.get('found') is False)):
        return True
    return False

def run(args, kit):
    op = args.get('op')
    key = _key(args.get('customer'))
    if key is None:
        return {'anonymous': True}
    path = _path(kit)
    db = _load(path)
    now = time.time()
    rec = db.get(key)
    is_new = rec is None
    if rec is None:
        rec = _new(now)
        db[key] = rec
    rec.setdefault('고정 정보', {'사는 동네':'', '최근 산 품목':[], '오늘 정보':'', '확인 시각':0})
    if op == 'get':
        if not is_new:
            try:
                last = float(rec.get('마지막 방문 시각', now))
            except (TypeError, ValueError):
                last = now
            if now - last > 1800:
                rec['방문 횟수'] = int(rec.get('방문 횟수', 1)) + 1
                rec['마지막 방문 시각'] = now
        _save(path, db)
        return _summary(rec, is_new)
    if op != 'update':
        return {'error': 'op must be get or update'}
    fields = args.get('fields') or {}
    if not isinstance(fields, dict):
        return {'error': 'fields must be an object'}
    for name, value in fields.items():
        if name == '고정 정보' and isinstance(value, dict):
            old = rec.get('고정 정보', {})
            new_fixed = {k: old.get(k, [] if k == '최근 산 품목' else (0 if k == '확인 시각' else '')) for k in FIXED}
            for k in FIXED:
                if k in value and not _empty(value[k], k):
                    new_fixed[k] = value[k]
            rec['고정 정보'] = new_fixed
        elif name not in FIELDS:
            continue
        elif name == '가격 민감도':
            if value in ('low', 'mid', 'high'):
                rec[name] = value
        elif name == '호칭':
            rec[name] = str(value)[:40]
        elif name in ('구매·흥정 이력', '선호 품목', '개인 맥락', '불만·환불'):
            old = rec.get(name, [])
            if not isinstance(old, list):
                old = []
            additions = value if isinstance(value, list) else [value]
            if name in ('개인 맥락', '불만·환불'):
                additions = [_say_only(v) for v in additions]
            rec[name] = (old + additions)[-20:] if name == '구매·흥정 이력' else (old + additions)[-10:]
    _save(path, db)
    return {'ok': True, 'customer': key}
