"""Build the cycle review without changing append-only harness records."""
import json,subprocess,collections
from pathlib import Path
P='meta/evidence/astra-1107-234803';A=P+'-audit.jsonl';V=P+'-verified.jsonl';Q=P+'-probes.jsonl'
rows=[(i,json.loads(l)) for i,l in enumerate(open(A),1)]
verified=[(i,json.loads(l)) for i,l in enumerate(open(V),1)]
probes=[(i,json.loads(l)) for i,l in enumerate(open(Q),1)]
evidence=[];grades=[];keep=[]
def cite(path,n,excerpt,meaning):
 assert excerpt in Path(path).read_text().splitlines()[n-1],(path,n,excerpt)
 evidence.append(dict(ref=f'{path}:{n}',excerpt=excerpt,meaning=meaning))
for i,r in rows:
 if r['kind']!='commit':continue
 b,a=r['before'],r['after'];sha=r['commit'];title=r['title'];files=r['files']
 reason=f"각 {b['hours']}/{a['hours']}h 유료판매 {b['paid']}→{a['paid']}, 구매지급 {b['buyer_paid']}→{a['buyer_paid']}, 숙성 {b['mature']}→{a['mature']}, 원가확인 숙성순기여 {b['mature_net']}→{a['mature_net']}원(누락 {b['missing_cost']}→{a['missing_cost']}), 시장정산 {b['market_paid']}→{a['market_paid']}. {A}:{i}. "
 grade='unclear';drop=False
 if title.startswith(('Merge','chore','docs')) or all('/test_' in f or f.endswith('.md') for f in files):
  grade='no_effect';reason+='기록·병합·검증 자체의 독립 경제효과는 주장하지 않는다. 관련 구현과 함께 평가하며 현재 최종 상태 유지.'
 elif title.startswith(('wip','revert')):
  grade='no_effect';reason+='거절·원복된 시도이므로 지속 처리군이 아니다. 원복 상태 유지.'
 elif sha=='b7aa9daa':
  grade='harmful';drop=True;reason+='도구쌍20회/24판단이나 최종 단위ID0건이며 기존 보충과 확대를 혼동했다. 손실액 인과관계는 미확정. 추가 GOAL 4줄을 이번에 삭제.'
 else:
  reason+='시장 수요·여러 동시 수정·숙성시간 차이로 단독 효과는 미확정. 결측: 해당 기능의 동시 비교군과 노출/품절시간. 측정: 이번 cutoff 원장 전후 집계. '
  if any(x in title.lower() for x in ['buyer','바이어','quote','food sku']):
   reason+='판정: 11/08 11:48 KST 동일 주문군100건에서 지급률·Saved/배정주문이 대조 이상, 잘못 지급0이면 유지; 미달이면 해당 신규 시험만 OFF, 사람의 oneshot은 유지.'
  else:
   reason+='판정: 11/08 11:48 KST 동종·등급 숙성40건에서 순기여/가용재고시간이 대조보다 높고 Players 비악화면 확대; 미달이면 해당 신규 시험만 중단, 보호 변경은 유지.'
 if sha in {'d1ba9c23','d6ae5558','7acdd73f'}:
  vi,x=next((vi,x) for vi,x in verified if x.get('commit')==sha)
  bb=x['before'].get('all') or {};aa=x['after'].get('all') or {};mm=x['after'].get('mature_6h') or {}
  reason+=f" SKU별 실제 적용 후 {bb.get('paid',0)}→{aa.get('paid',0)}판매, {bb.get('net')}→{aa.get('net')}원(뒤쪽 미숙성 포함), 숙성 {mm.get('paid',0)}건. {V}:{vi}. 11/08 08:00 숙성10건·시간당 순기여 개선 판정; 미달이면 추가축소 중단하고 원래 target 복원을 검토하되 지속 SKU손실이면 근거로 유지."
 if sha in {'f0581bcb','6ab923b4','81d52783','6ff52677','2f704719','997c09ba','a94bcea4','639bad15'}:
  reason+=' macOS 임시경로27실패가 다른 변경 승인을 막았다. 실행기 안전검사는 유지하고 fixture 경로만 resolve하여14테스트 통과; 금융효과와 별개다.'
 grades.append(dict(change=sha+' '+title,grade=grade,reason=reason))
 keep.append(dict(item=sha+' '+', '.join(files),decision='drop' if drop else 'keep',reason=('restock.GOAL 추가4줄을 실제 제거. 나머지 도구는 유지.' if drop else '현재 적용·원복·비활성 상태 유지. '+('독립 수익효과를 단정하지 않으며 위 표본·기한·철회선으로 재판정.' if grade=='unclear' else '실패한 과거 상태를 다시 켜지 않는다.'))))
 cite(A,i,'"before": '+json.dumps(b,ensure_ascii=False),sha+' 이전 정산/숙성 원가확인 순기여; 원장 출처 sources 포함')
 cite(A,i,'"after": '+json.dumps(a,ensure_ascii=False),sha+' 이후 정산/숙성 원가확인 순기여; 부분기간·누락 확인')
# Every recorded strategy check and change since previous review; old baseline is audited as well.
for i,r in rows:
 if r['kind']!='cycle':continue
 cycle=r['cycle'];grade='unclear'
 reason='Patch3.5 인지·forum 원값·Players 목표·Luna 무상한 유지. 두 완료창 stock cost절단0/0이므로 총상한 미확대는 타당. 경쟁 속도에 근거한 현행 유지도 타당하나 독립 추가ΔPlayers는 미식별.'
 if r['status']=='rejected':grade='harmful';reason+=' 인용 또는 반복 테스트 실패로 전달 실패. 기존 경로 문제를 고치지 않고 같은 SKU수정을 재시도한 검토 품질은 감점.'
 if cycle=='terra-1107-134856':grade='harmful';reason+=' 신규 맥락이 보충과 확대를 혼동하게 했다. 이번 철회.'
 if cycle in ['terra-1107-150151','terra-1107-183826']:grade='no_effect';reason+=' 직접 조회는 수행했으나 판단 혼동을 해소하지 못했다. 18:38의 formula 보충 때문에 실손실 미확정이라는 구분은 타당.'
 if cycle.startswith('astra'):reason+=' 지난 Astra의 맥락 가설도 이번20/24호출·ID0건으로 반증되어 철회 대상. 도구 자체 판매ID 계산과 경제적 효과를 구분한다.'
 reason+=' 현재 yms979 A/B72h카드와 반복 무보증 설명은 불일치하여 정정. 결측: 실행 공급손실; 이번24판단 감사 및 commit 경계 코호트 측정. 다음11/08 11:48,3판단 구분3/혼동0 아니면 새 구분문장 철회.'
 grades.append(dict(change=cycle+' 전략·패치·확대·이전판정',grade=grade,reason=reason))
 cite(A,i,'"cycle": '+json.dumps(cycle),r['ref']+' 원본 변경/전략 평가')
# Tools, including absent historical names, and standalone owner/measurement utilities.
p=json.load(open(P+'.json'))
tools={r['name']:(i,r) for i,r in rows if r['kind']=='tool'}
for name in sorted(set(tools)|set(p['tools'])):
 live=Path('tools',name,'tool.py').exists();i,r=tools.get(name,(None,{}));bad=name=='auto_market_research_context'
 why=f"이번 {r.get('calls',0)}호출/{r.get('failed',0)}실패. "
 if bad:why+='정상종료해도 catalog1/기대결제1을 ledger0으로 반환. tool.py·manifest 실제 삭제, 별도 테스트 없음; 원본 보존.'
 elif not live:why+='이미 제거된 도구는 다시 설치하지 않는다.'
 else:why+='현재 구현 유지. 사용0이면 기여는 미확정이고 추가 확장하지 않는다. supply_audit의 판단문구 실패와 도구 자체 계산은 구분.'
 keep.append(dict(item='tools/'+name,decision='drop' if bad else 'keep',reason=why))
 if i:cite(A,i,'"name": '+json.dumps(name),why)
for item in ['tools/quote_owner_executor.py','tools/quote_alignment_measure.py']:
 keep.append(dict(item=item,decision='keep',reason='수동 로컬 준비/오프라인 측정 도구이며 이번에 실행·배포하지 않았다. 임시 저장소 테스트로 안전검사 유지 확인.'))
grades.append(dict(change='auto_market_research_context',grade='harmful',reason='기대 유료1→반환0, 실행코드0으로 selftest만 통과할 수 있는 의미오류. verified:1 재현 후 도구와 manifest 삭제.'))
grades.append(dict(change='test_quote_owner_executor 경로 수정',grade='worked',reason='실제 실행기 검사 유지, macOS fixture만 resolve: 수정 전 전체948테스트27실패, 수정 후 대상14테스트 통과. 전체 결과는 tests-after.log에서 확인.'))
for path,n,ex,why in [(V,1,'"ledger_count": 0','정상실행 의미 오류'),(V,2,'"paid": 2766','과거/현재 터널 합친 경쟁 판매 하한'),(V,7,'"paid": 21','청소기 수요의 실제 시장 결제'),(V,8,'"paid": 10','계란 수요의 실제 결제'),(Q,1,'"objective_matches": true','공식 모든 패치 재확인, 변경없음'),(Q,26,'"unit_id_in_why": false','현재 보충과 확대 혼동 사례'),(P+'-measurements.jsonl',1,'"stock_cost_cuts": 0','첫 창 총상한 비구속'),(P+'-measurements.jsonl',2,'"stock_cost_cuts": 0','둘째 창 총상한 비구속')]:cite(path,n,ex,why)
for i,r in verified:
 if r['kind']=='sku_change':cite(V,i,'"after": '+json.dumps(r['after'],ensure_ascii=False),r['sku']+' 적용 이후 손익과 숙성 표본')
for path in [P+'-tests-before.log',P+'-owner-tests.log',P+'-tests-after.log']:
 for i,l in enumerate(open(path),1):
  if l.startswith(('Ran ','FAILED')):cite(path,i,l.strip(),'지정 환경 검증 원문')
opts=[dict(direction='현행 상품·협상·Saved 유지, 보충과 확대의 구분 복구',expected_profit='추가ΔPlayers 기준0; 문구 개선 인과 기대값 미식별. 경쟁3h(실제4h)/12.2h 연장 시12h 후 선두격차0/.04869, 바로위−.083/−.01134.',risk='문구만 개선해도 실제 공급이 달라지지 않으면 경제효과0. formula 보충과 혼동 금지.',test='기존 맥락 철회 후 새 구분문장을 순차 실행;3판단 구분3/혼동0.'),dict(direction='검증된60만+ SKU 한 단위 확대',expected_profit='기존정책 외 book66.851M·매출176.002M 추가 문턱을 넘을 때 조건부+.01379. 경쟁자는 같은3h/12h 속도로 이동. 총상한 절단0/0이므로 총상한만 인상할 추가효과0.',risk='환불·서지·보관료와 현금완충. 수익성 검증 없이 전체 확대 불가.',test='target 구속13/14행을 SKU로 분리, 두창 판매·추가분 잔류·6h 숙성/비하락 확인.'),dict(direction='Saved 개선과 정확한 SKU 수요 연결',expected_profit='Saved+.0055 추가면+.00909, forum+.003 추가면+.0009로 조건부+.00999. 675추가거래에 거래당4.31%p의 기존정책 외 절약 필요. 경쟁속도 동일, 인과 기대값 미식별.',risk='수요19/11회는 자기보고, 청소기 구매자도이미9회결제. 유료투표는book손실.',test='3수요글과 실제SKU·구매자시각·현재재고 연결, 오연결0/유효후보1. 메시지나 매입 없음.')]
report=dict(changed=True,hypothesis='재입고 맥락의 확대/보충 혼동과 빈 결제검색 도구를 제거하고 테스트 환경을 바로잡으면, Terra가 유효한 수정과 근거 있는 규모 판단을 실행할 수 있다.',change_summary='실패한 restock.GOAL4줄과 자동도구1개 삭제, macOS 테스트 임시경로 정규화, 전략·Terra 지침·직렬실험·보고서 갱신. 가격·재고총량·보호설정은 변경하지 않았다.',target_metric='Everyone Players 순위; 보조: 혼동 거부0, 의미검색 누락0, 전체검증 실패0',expected='직접 추가 Players 효과는 미식별. 다음3판단 구분3/혼동0, 테스트27실패 해소, 결제1건 누락 도구 제거.',evidence=evidence,evidence_gap=dict(missing='맥락 오류가 실제 공급량·숙성 순기여·Players에 미친 독립 효과; SKU축소 후 숙성표본',measurement='114커밋 전후 원장/시장정산,24재입고판단,두공급창 절단0/0,실제 적용시각 SKU코호트,정상종료 fixture 결제1→0 측정.',judge_line=dict(metric='기존보충과 목표확대를 구분한 판단수 및 혼동거부수',direction='above',threshold='구분>=3 및 혼동거부=0',min_sample='서로 다른 재입고판단3건',expires='2026-11-08 11:48 KST',undo='새 구분문장만 철회. 기존 실패 문장은 이미 제거했고 기존 실험을 Terra가 drop으로 닫는다. 총량·보호설정 유지.')),summary='Players1.624,6/62,이전대비+.052. 114커밋·10 Terra주기와 지난Astra를 검토했다. 테스트 및 세부 등급은 본 파일과 원본 감사 참조. 커밋·push·PR 없음.',strategy=dict(options=opts,chosen='현행 유지. 다른 방향의 추가ΔPlayers 우월성이 입증되지 않아 보충/확대 구분을 첫 실험,현재 미충족 수요 연결을 작은 두 번째 실험으로 둔다.',experiment='기존 supply-audit-context를 다음 Terra가 drop 기록한 뒤 replenish-vs-expand-234803, 다음 forum-demand-join-234803을 하나씩12h 실행.'),grades=grades,keep_or_drop=keep)
Path(P+'-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=1)+'\n')
with open(P+'-settings.jsonl','w') as f:
 for _,r in rows:
  if r['kind']=='commit':f.write(json.dumps(dict(commit=r['commit'],files=r['files'],pricing_diff=subprocess.check_output(['git','show',r['commit'],'--format=','--','pricing.json'],text=True),decision='현재 상태 유지; b7aa9daa의 GOAL추가만 이번 철회'),ensure_ascii=False)+'\n')
print(json.dumps(dict(commits=114,grades=len(grades),decisions=len(keep),citations=len(evidence)),ensure_ascii=False))
