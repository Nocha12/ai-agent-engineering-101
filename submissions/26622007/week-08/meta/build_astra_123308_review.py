"""Complete review with exact file-line citations, preserving failed attempts and logs."""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from meta_harness import verify_evidence
BASE='meta/evidence/astra-1108-123308'
AUDIT=BASE+'-audit-v2.jsonl'
rows=[(i,json.loads(l)) for i,l in enumerate(open(AUDIT),1)]
grades=[];keep=[];evidence=[]
def cite(path,n,meaning,excerpt=None):
    with open(path) as f: line=next(l for i,l in enumerate(f,1) if i==n).rstrip('\n')
    evidence.append(dict(ref=f'{path}:{n}',excerpt=excerpt or line,meaning=meaning))
    return f'{path}:{n}'
judges={
 'buyer':'2026-11-09 00:33 KST까지 동종100주문에서 Saved/주문 및 지급률이 대조 이상, 잘못된 지급0이면 유지; 미달은 해당 실험 경로만 중단 검토, 표본미달은 확대 금지',
 'shop':'2026-11-09 00:33 KST까지 동일SKU·등급 숙성40건의 순기여/시간이 기존정책 초과이고 Players 비악화면 확대; 미달·표본부족이면 추가 확대 금지',
 'test':'2026-11-09 00:33 KST까지 변경 경로의 회귀실패0, 실제적용3건에서 계약위반0이면 유지; 실패는 해당 경로 수리, 표본부족은 수익효과 미확정',
}
for i,r in rows:
 if r['kind']!='commit':continue
 b,a=r['before'],r['after'];sha=r['commit'];title=r['title'];files=r['files']
 reason=f"전후{b['hours']}/{a['hours']}h 정산{b['paid']}→{a['paid']}, 환불후 확인원가 순기여{b['mature_net']}→{a['mature_net']}원, 숙성{b['mature']}→{a['mature']}, 원가누락{b['missing_cost']}→{a['missing_cost']}, 구매지급{b['buyer_paid']}→{a['buyer_paid']}, 시장정산{b['market_paid']}→{a['market_paid']} ({AUDIT}:{i}). "
 grade='unclear';kind='buyer' if any('buyer' in f for f in files) else 'shop'
 if title.startswith(('chore','Merge','revert','wip','docs')):
  grade='no_effect';reason+='기록·병합·거절원복 자체는 독립 운영효과가 없다. 원본 변경과 현재 상태를 따로 채점한다.'
 else:
  if title.startswith('test') or 'tests:' in title:kind='test'
  reason+=f"결측: 동시변경·시장수요를 분리한 독립 비교군. 이번 측정: 원장 전후30분 및 주문연결 환불 집계. 판정: {judges[kind]}."
 if sha=='b7aa9daa':
  grade='harmful';reason+='필수 감사 지침 뒤 최근3판단 중3건이 감사결측·총재고를 전체보류 이유로 사용하여 목표0건 실패. 이번에 해당4줄 제거. formula 보충 때문에 실제 공급 전체중단으로 해석하지 않는다.'
 if sha=='b882dcbc':reason+='공급 감사 도구는43호출0실패였지만 뒤의 강제맥락 실험은 실패했다. Astra의 설계도 감점한다.'
 if sha=='d1ba9c23':reason+='묶여 들어간 auto_market_research_context는 실제정산1건→검색0건으로 별도 harmful 판정·삭제. SKU4→3은 당시숙성손실 근거가 있으나 사후독립효과는 미확정.'
 grades.append(dict(change=sha+' '+title,grade=grade,reason=reason))
 keep.append(dict(item=sha+' '+', '.join(files),decision='keep',reason='현재 HEAD의 적용·원복 상태를 유지한다. 아래 명시적 drop인 restock 필수감사4줄과 auto_market_research_context는 제외한다. '+('거절·중단 경로를 다시 켜지 않는다.' if grade=='no_effect' else '위 판정선 전에는 성과를 확정하거나 확대하지 않는다.')))
 cite(AUDIT,i,sha+' 전후 원장 정산·성숙순기여; 원본행은 sources에 기록', '"before": '+json.dumps(b,ensure_ascii=False)+', "after": '+json.dumps(a,ensure_ascii=False))
# Exact file/settings inventory for every commit, not just Terra changes.
with open(BASE+'-settings.jsonl','w') as out:
 for i,r in rows:
  if r['kind']!='commit':continue
  diff=subprocess.check_output(['git','show',r['commit'],'--format=','--','pricing.json'],text=True)
  out.write(json.dumps(dict(commit=r['commit'],files=r['files'],pricing_diff=diff,decision='현재 HEAD 유지; 명시 drop은 review keep_or_drop 참조'),ensure_ascii=False)+'\n')
# Every meta cycle, including the failed overnight Astra review.
for i,r in rows:
 if r['kind']!='cycle':continue
 a=r.get('answer') or {};cycle=r['cycle'];grade='unclear'
 reason='규칙은 Patch3.5 원값포럼·Players와 일치하고 신규패치는 없었다. 호출상한 복원이나 총량축소는 확인되지 않았다. 총상한1B의 두창구속 근거가 없어 미인상 자체는 감점하지 않는다. '
 if r['status']=='rejected':
  grade='harmful';reason+='실패한 검증/인용으로 수정이 반복 원복됐다. 문제 발견과 운영 반영은 다르며 실패를 해소하지 못한 검토를 감점한다. '
 elif cycle=='terra-1107-134856':
  grade='harmful';reason+='실험 시작은 큐를 지켰으나 필수 감사 맥락이3/3 전체보류 판단을 만들어 실패했다. '
 else:
  reason+='전략 유지의 상대 성장 비교는 기록했으나 재입고 판단과 실제 공급의 차이, 변경별 인과효과가 분리되지 않았다. '
 reason+='결측: 변경별 독립효과와 당시 실제 target 구속. 측정: 전수 원장/두창 절단0/0·최근3판단 전체보류3건. 판정: 11/09 00:33 KST, 변경후3판단 전체보류0·현금완충위반0, 미달은 새맥락만 철회. 전략은 두연속3h/12h 상대열세와 대안 우월성 확인 때 watch→shift. '
 reason+=f"원문 {r['ref']}; 해당 주기의 숫자·전략 원문은 {AUDIT}:{i}."
 grades.append(dict(change=cycle+' 전략·규칙·확대·검증 점검',grade=grade,reason=reason))
 cite(AUDIT,i,cycle+' 전체 변경·전략점검 원문','"cycle": '+json.dumps(cycle))
# Post-change cohorts for the three surviving Terra setting changes.
probes=[json.loads(l) for l in open(BASE+'-probes-v2.jsonl')]
for n in (0,2,4):
 b,a=probes[n:n+2];sku=b['sku'];rates=[round(x['known_net']/x['hours'],2) for x in (b,a)]
 reason=f"숙성판매 {b['paid']}→{a['paid']}, 순기여 {b['known_net']}→{a['known_net']}원, 시간당{rates[0]}→{rates[1]}원, 건당{b['net_per_sale']:.2f}→{a['net_per_sale']:.2f}원. 전6h와 후{a['hours']:.2f}h로 길이가 다르고 시장수요·등급구성이 혼재한다. 결측: 같은시각대 대조·재고시간. 측정: 실제 변경시각 판매코호트와6h환불. 판정: 11/09 00:33 동일SKU40숙성의 시간당기여 대조초과 및두창판매조건이면 확대, 부족하면 현 target 유지."
 grades.append(dict(change=sku+' Terra target 축소 사후효과',grade='unclear',reason=reason))
 for j in (n,n+1):cite(BASE+'-probes-v2.jsonl',j+1,sku+' 변경 전후 숙성표본')
# All currently registered and historically reported tools.
p=json.load(open(BASE+'.json'))
toolrows={r['name']:(i,r) for i,r in rows if r['kind']=='tool'}
for name in sorted(set(toolrows)|set(p['tools'])):
 live=Path('tools',name,'tool.py').exists();i,r=toolrows.get(name,(None,{}));usage=p['tools'].get(name,{})
 reason=f"검토구간{r.get('calls',0)}호출/{r.get('failed',0)}실패, 팩누계{usage.get('calls',0)}호출/{usage.get('failed',0)}실패. "
 if name=='auto_market_research_context':
  reason+='상품명→SKU 결제연결 실패를 재현하여 tool.py와 manifest/selftest 삭제; 전용 테스트 없음. 원본은 dropped-tool.json에 보존.';decision='drop'
 elif not live:reason+='기존 삭제 상태를 유지하며 재등록하지 않는다.';decision='keep'
 else:reason+='도구 유지. 실행성공은 의미정확성·수익효과의 보장이 아니며 사용0 도구를 추가 생성하지 않는다.';decision='keep'
 if name=='supply_audit':reason+='43호출0실패. 실패한 것은 강제맥락; 관측누락을0매입 확정으로 쓰지 않는다.'
 keep.append(dict(item='tools/'+name,decision=decision,reason=reason))
 if i:cite(AUDIT,i,name+' 사용/실패 집계')
keep.extend([
 dict(item='restock.GOAL supply_audit/cohort_profit 필수 지침4줄',decision='drop',reason='최근3판단 전체보류3건으로0건기준 실패. 이번 실제 제거; 도구·총량은 유지.'),
 dict(item='meta/experiments.json forum-demand-join-113730',decision='drop',reason='미시작이고 수요 근거가 하루 이상 오래됨. 해당 큐항목을 실제 제거하고 fresh-demand-join-123308으로 교체.'),
 dict(item='pricing.json 전체 및 사람의 거절목록·Luna 무상한·파산방지',decision='keep',reason='이번 수치 변경0. 두창 stock cost 절단0/0, SKU별 확대세조건도 미충족. 사람의 고정값 그대로.'),
 dict(item='test_quote_owner_executor.py 임시경로 resolve 및 meta/evidence_citations.py',decision='keep',reason='경로 별칭으로14테스트27실패 재현 후 정규화. 전체1010테스트통과, 인용 검증/파일변경 감지 추가.'),
 dict(item='meta/strategy.md, meta/terra.md 및 새 직렬실험2개',decision='keep',reason='최신 규칙과 상대3h/12h속도, 실패실험철회·정상보충 판정선 반영. 새실험은 Terra가 앞실험을 닫고 하나씩 시작.')])
grades.extend([
 dict(change='auto_market_research_context 의미검증',grade='harmful',reason='기대 실제정산1건인데 반환0건. 실행실패0만으로 의미정확성을 인정한 tool_forge 선택을 감점하고 도구를 제거했다.'),
 dict(change='이번 임시경로 정규화와 실제행 인용',grade='worked',reason='변경전14테스트27실패→변경후전체1010테스트통과. 운영 검증을 막던 경로문제를 해결했다. 추가Players는 미측정.'),
 dict(change='반복 인용실패 원인',grade='unclear',reason='보존된 c9857e16 로그에는 OK가1577행이나 당시검사내용은 없다. 결측: 검증시점 원본. 측정: 커밋의 로그를 복사해 실제행 재검증. 판정: 다음3주기 citation rejection0,11/09 00:33판정; 재발시 검사시점해시/내용 증거를 보강하고 인용검증을 완화하지 않는다.')])
for path,n,meaning in [(BASE+'-tool-probe.jsonl',1,'결제검색 fixture 1건 누락'),(BASE+'-measurements.jsonl',1,'첫 창 총상한절단0'),(BASE+'-measurements.jsonl',2,'둘째 창 총상한절단0'),(BASE+'-measurements.jsonl',4,'재입고 전체보류 첫 판단'),(BASE+'-measurements.jsonl',5,'재입고 전체보류 둘째 판단'),(BASE+'-measurements.jsonl',6,'재입고 전체보류 셋째 판단'),(BASE+'-probes-v2.jsonl',14,'공개 규칙과 목적함수 일치')]:cite(path,n,meaning)
for path in (BASE+'-tests.log',BASE+'-path-before.log'):
 for n,line in enumerate(open(path),1):
  if line.startswith(('Ran ','FAILED')):cite(path,n,'실제 종료한 테스트 결과',line.strip())
opts=[
 dict(direction='현행 폭넓은상품·검증고가·Saved전략과 정상보충 복구',expected_profit='계획 추가ΔPlayers0, 인과 기대값 미확정. 3.4h속도12h연장시 선두격차.08382/바로위.04076,12.8h속도면.00281/.02775. 전체보류3/3→0/3을 먼저 확인.',risk='빈 agent매입에도 formula가 보충하므로 판단회복이 추가매출과 동일하지 않다. 현금완충·숙성손실 조건 유지.',test='감사4줄제거 후3판단·2공급창, 전체보류0·완충위반0; 다음 Terra가 정상보충 구분 문장 시험.'),
 dict(direction='고가 총량 확대·회전 가속',expected_profit='현행대비 추가book80,959,758원과매출50,375,461원 문턱을 모두 넘으면+.01379, 미식별 기대값. 경쟁자3h/12h이동은 동일 반영. 총상한절단0/0이라 단순상한인상 효과0.',risk='환불·서지·보관비; 재고매입을book증가로 오인할 위험. 세조건없는일괄확대 금지.',test='SKU별 실제target절단·두창단위판매·6h숙성순기여 및book을 확인하고 조건충족SKU만 한단계.'),
 dict(direction='구매Saved와 최신 미충족포럼 수요 연결',expected_profit='Saved .2917→.2944와forum .888→.889가 현행이상 추가개선이면+.00939. 경쟁자이동은 위두시나리오. 인과기대값 미식별이라 무료연결조사만 두번째실험.',risk='예산·잔여기한 미달, 포럼 주장과 실제정산SKU 불일치. 매입·게시·유료투표 자동실행 없음.',test='최신수요3개, 예산·유통기한·SKU·정산 검증후보>=1, 오연결0; 시작후12h판정.')]
report=dict(changed=True,hypothesis='목표 확대에 필요한 증거를 일반 보충의 필수조건으로 오해하게 만든 지침과 결제검색 오류를 제거하면 근거없는 보충보류를 줄이고 Terra의 복구를 실제 적용할 수 있다.',change_summary='실패한 restock 감사지침4줄과 자동검색도구1개 제거, 테스트 임시경로 정규화, 실제행 인용 도구·회귀테스트 추가. 전략·Terra·실험큐와 보고서 갱신. 보호설정 변경없음.',target_metric='Everyone Players 순위; 보조지표는 근거없는 전체보충보류·의미검색실패·검증거절 건수',expected='12h 추가Players는 미확정(계획0). 재입고3판단 전체보류3→0, 검증거절0, 검색결제누락도구1개 제거를 기대한다.',evidence=evidence,evidence_gap=dict(missing='지침제거의 실제공급·숙성순기여·추가Players 독립효과와 반복인용거절의 검사시점파일',measurement='165커밋 전후 원장정산·환불후순기여,22메타주기,65로그 완독해시,두창절단0/0,3판단전체보류3건,검색fixture1→0,변경전27실패와전체1010통과를 측정했다.',judge_line=dict(metric='감사결측 또는 총재고부담만을 이유로 한 전체 재입고 보류',direction='below',threshold='1건; 현금완충위반0',min_sample='복구후 재입고3판단·완료공급창2개',expires='2026-11-09 00:33 KST',undo='표본부족은미확정,실패면새구분문장만제거하고원인재측정. 폐기한필수감사지침·전체규모축소는복구하지 않는다.')),summary='Players10위1.572→6위1.639. 최근3h하락으로 실행복구우선. 165커밋과22메타주기 전수판정은 이 파일에 보존했다. 전체1010테스트통과. 커밋·push·PR없음.',strategy=dict(options=opts,chosen='현행전략유지·정상보충 실행복구우선. 다른방향의 추가ΔPlayers 우월성이 입증되지 않았다. 최신수요 연결은 두번째 작은 실험.',experiment='이미실패한 supply-audit-context-113730은 지침을제거했으며 다음Terra가drop기록. 그뒤ordinary-refill-context-123308→fresh-demand-join-123308을 각각12h씩 직렬실행. 큐의지표·표본·undo로판정.'),grades=grades,keep_or_drop=keep)
problems=verify_evidence(evidence)
if problems:raise RuntimeError(problems)
Path(BASE+'-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=1)+'\n')
print(json.dumps({'grades':len(grades),'keep_or_drop':len(keep),'evidence':len(evidence),'citation_problems':problems},ensure_ascii=False))
