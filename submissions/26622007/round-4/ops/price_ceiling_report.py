from html import escape
from pathlib import Path
from runpy import run_path


OPS = Path(__file__).resolve().parent


def amount(value):
    return '불가' if value is None else format(value, ',')


def product(row):
    return f"{row['name']} ({row['sku']})"


def table(caption, headers, rows):
    parts = [f'<div class="scroll" role="region" aria-label="{escape(caption)}" tabindex="0">',
             f'<table><caption>{escape(caption)}</caption><thead><tr>']
    parts.extend(f'<th scope="col">{escape(label)}</th>' for label in headers)
    parts.append('</tr></thead><tbody>')
    for label, *values in rows:
        parts.append(f'<tr><th scope="row">{escape(str(label))}</th>')
        parts.extend(f'<td>{escape(str(value))}</td>' for value in values)
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


def render(build_rows):
    baseline = build_rows(hold_hours=1)
    prices = table('기본 조건에서 품목별 가격과 위험',
                   ('품목 (SKU)', '매입 상한', '판매 하한', '기대손익', '보수적 매입 상한',
                    '보수적 판매 하한', '미판매율', '위험'),
                   ((product(row), amount(row['ceiling']), amount(row['floor']),
                     amount(row['profit']), amount(row['conservative_ceiling']),
                     amount(row['conservative_floor']), format(row['unsold'], '.1%'),
                     '보수 기준 미달' if row['risk'] else '보수 기준 충족') for row in baseline))
    inputs = table('계산에 쓴 가격과 출처',
                   ('품목 (SKU)', '정가', '판매가', '판매가 출처', '원가', '원가 출처'),
                   ((product(row), amount(row['list_price']), amount(row['sell']),
                     row['sell_source'], amount(row['cost']), row['cost_source'])
                    for row in baseline))
    scenarios = [('1시간 · 기본 미판매율', baseline),
                 ('24시간 · 기본 미판매율', build_rows(hold_hours=24)),
                 ('1시간 · 미판매율 0%', build_rows(hold_hours=1, unsold=0)),
                 ('1시간 · 미판매율 25%', build_rows(hold_hours=1, unsold=0.25))]
    bounds = [{row['sku']: row for row in rows} for _, rows in scenarios]
    sensitivity = table('조건별 매입 상한 (크레딧/개)',
                        ['품목 (SKU)'] + [label for label, _ in scenarios],
                        ([product(row)] + [amount(by_sku[row['sku']]['ceiling']) for by_sku in bounds]
                         for row in baseline))
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>품목별 매입 상한과 판매 하한</title><style>
body{{font:1rem/1.6 system-ui,sans-serif;color:#172332;background:#fff;max-width:84rem;margin:auto;padding:1.25rem}}
h2{{margin-top:2rem}} .scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%}}
caption{{font-weight:bold;text-align:left}} th,td{{border:1px solid #a8b3ba;padding:.4rem .65rem;text-align:right}}
thead,tbody th{{background:#edf2f5}} tbody th,thead th:first-child{{text-align:left}}
</style></head><body><main><h1>품목별 매입 상한과 판매 하한</h1>
<p>매입할 때는 상한 이하인지, 판매할 때는 하한 이상인지 확인하세요. 가격 단위는 개당 크레딧입니다.
‘불가’는 해당 조건에서 계산기가 제시하는 가격이 없다는 뜻이며 0크레딧이 아닙니다.</p>
<h2>기본 조건과 가정</h2><p>이 표의 조건은 수량 1개, 묶음 1개, 등급 B, 보유 1시간입니다. SKU별 최근 결제 속도를 적은 표본에서 수축하고 경쟁 점유율·기존 재고·판매 가능 시간으로 판매 확률을 계산합니다.
보수 기준은 점유율을 절반으로 줄이고 식품 잔여시간을 등급 구간 하단으로 잡습니다. 마감이 보유시간보다 늦다고 가정하며, 미판매 재고의 마감 가치는 0입니다.
기대손익은 이 조건과 아래 입력 가격을 사용한 추정치입니다. 정가는 판매가나 실제 거래가가 아닙니다.</p>
{prices}
<h2>판매가와 원가의 근거</h2><p>입력 가격이 관측값인지 추정값인지 출처를 확인하세요.
과거 가격은 다음 주문의 확정 가격이 아닙니다.</p>{inputs}
<h2>보유 시간과 미판매율에 따른 민감도</h2><p>기본 조건에서 한 번에 한 가지 가정만 바꿉니다.
같은 품목을 가로로 읽어 더 오래 보유하거나 일부를 팔지 못할 때의 매입 상한을 비교하세요.</p>{sensitivity}
<h2>위험과 한계</h2><p>식품은 유통기한이 지나면 판매하지 못할 수 있고, 비식품은 보유 시간에 따라 보관료와 고장 위험이 달라집니다.
보수적 상한·하한도 가정에 따른 값입니다. 수요, 판매 성사, 환불 여부 또는 실제 이익을 보장하지 않습니다.</p>
</main></body></html>'''


def main():
    html = render(run_path(str(OPS / 'price_ceiling.py'))['build_rows'])
    output = OPS.parent / 'logs' / 'price-ceiling.html'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
