import json, os, re, time

FIELDS = ['호칭', '구매·흥정 이력', '선호 품목', '가격 민감도', '개인 맥락', '불만·환불']

def _path(kit):
    return 'customers.test.json' if getattr(kit, 'testing', False) else 'customers.json'

def _load(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            x = json.load(f)
        return x if isinstance(x, dict) else {}
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
    return {'호칭':'', '구매·흥정 이력':[], '선호 품목':[], '가격 민감도':'mid', '개인 맥락':[], '불만·환불':[], '방문 횟수':1, '마지막 방문 시각':now}

def _summary(rec):
    if not rec:
        return {'new': True}
    return {
        '호칭': rec.get('호칭',''),
        '방문 횟수': rec.get('방문 횟수',1),
        '마지막 방문': rec.get('마지막 방문 시각'),
        '최근 구매 2건': rec.get('구매·흥정 이력',[])[-2:],
        '선호 품목 3개': rec.get('선호 품목',[])[:3],
        '가격 민감도': rec.get('가격 민감도','mid'),
        '개인 맥락 키워드 2개': rec.get('개인 맥락',[])[-2:]
    }

def _say_only(value):
    s = str(value)
    return s if s.startswith('손님 말:') else '손님 말:' + s

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
        return {'new': True} if is_new else _summary(rec)
    if op != 'update':
        return {'error': 'op must be get or update'}
    fields = args.get('fields') or {}
    if not isinstance(fields, dict):
        return {'error': 'fields must be an object'}
    for name, value in fields.items():
        if name not in FIELDS:
            continue
        if name == '가격 민감도':
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
    # 방문 횟수와 시각은 도구가 관리하며 update 입력으로 덮어쓰지 않는다.
    _save(path, db)
    return {'ok': True, 'customer': key}
