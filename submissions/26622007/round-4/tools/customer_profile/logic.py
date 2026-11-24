import re
import httpx

def _name(customer):
    raw = str(customer or '')
    if 'a person in your app' in raw.lower() or 'did not say who it is' in raw.lower():
        return None
    m = re.search(r"says it is\s+(['\"])(.*?)\1", raw, flags=re.I)
    return (m.group(2) if m else raw).strip().strip(" \t\r\n'\"[]")[:40]

def _get(url):
    r = httpx.get(url, timeout=12)
    r.raise_for_status()
    return r.json()

def run(args, kit):
    name = _name(args.get('customer'))
    if not name:
        return {'found': False, '오늘 정보': ''}
    base = kit.market.rstrip('/')
    result = {'found': False, '사는 동네': '', '최근 산 품목': [], '오늘 정보': ''}
    try:
        village = _get(base + '/api/village')
        town = village.get('in_town', []) if isinstance(village, dict) else []
        if isinstance(town, list):
            for person in town:
                if isinstance(person, dict) and person.get('name') == name:
                    result['사는 동네'] = person.get('lives_in', '') or ''
                    break
        today = village.get('today', {}) if isinstance(village, dict) else {}
        if isinstance(today, dict):
            result['오늘 정보'] = {k: today.get(k) for k in ('weekday','part','weather','market_day','payday') if k in today}
        else:
            result['오늘 정보'] = today
    except Exception:
        pass
    try:
        ledger = _get(base + '/api/ledger?after=-500')
        rows = ledger if isinstance(ledger, list) else ledger.get('ledger', ledger.get('rows', [])) if isinstance(ledger, dict) else []
        matches = []
        for row in rows:
            if not isinstance(row, dict) or row.get('kind') != 'settle':
                continue
            data = row.get('data', {})
            if not isinstance(data, dict) or data.get('buyer') != name:
                continue
            shop = str(data.get('shop') or '').rstrip('/').lower()
            ours = str(getattr(kit, 'shop_url', '') or '').rstrip('/').lower()
            matches.append({'name': data.get('name', ''), 'grade': data.get('grade', ''), 'shop': '우리 가게' if ours and shop == ours else '다른 가게'})
        if matches:
            result['found'] = True
            result['최근 산 품목'] = matches[-3:]
    except Exception:
        pass
    if not result['found']:
        return {'found': False, '오늘 정보': result['오늘 정보']}
    return result
