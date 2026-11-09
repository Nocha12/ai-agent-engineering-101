"""Build per-change review and exact-line citations; never alter append-only journals."""
import json
from pathlib import Path
C='astra-1109-133041'; A=f'meta/evidence/{C}-audit.jsonl'; P=f'meta/evidence/{C}-probes.jsonl'; PACK=f'meta/evidence/{C}.json'
rows=[(i,json.loads(l),l.rstrip()) for i,l in enumerate(open(A),1)]
p=json.load(open(PACK)); grades=[]; keep=[]; evidence=[]
def cite(path,line,excerpt,meaning):
 actual=Path(path).read_text().splitlines()[line-1]
 assert excerpt in actual,(path,line,excerpt)
 e=dict(ref=f'{path}:{line}',excerpt=excerpt,meaning=meaning)
 if e not in evidence:evidence.append(e)
def find(path,needle,meaning):
 for i,line in enumerate(Path(path).read_text().splitlines(),1):
  if needle in line:cite(path,i,needle,meaning);return f'{path}:{i}'
 raise ValueError((path,needle))
for i,r,line in rows:
 if r['kind']!='commit':continue
 b,a=r['before'],r['after']; title=r['title']; sha=r['commit']
 metrics=f"유료판매 {b['paid']}→{a['paid']}, 구매지급 {b['buyer_paid']}→{a['buyer_paid']}, 숙성순기여 {b['mature_net']}→{a['mature_net']}원(누락 {b['missing_cost']}→{a['missing_cost']}, 숙성 {b['mature']}→{a['mature']}), 시장정산 {b['market_paid']}→{a['market_paid']} (전후각 {b['hours']}/{a['hours']}h; 보관비 제외)."
 grade='unclear'; reason=''
 if title.startswith(('chore(', 'revert(', 'wip(')):
  grade='no_effect';reason='기록/실패/원복 자체는 독립적인 매매 개선이 아니다. 실패 기록과 현재 원복 상태를 보존한다.'
 elif sha=='47ff6ef2':
  grade='worked';reason='잘못된 검색 문맥 도구를 실제 제거했다. 제거 목적은 달성했으나 거래증가는 별도 인과 검증이 필요하다.'
 elif sha=='ab2ad9fb':
  grade='worked';reason='기한 종료된 수요 맥락을 실제 제거했다. 미지원 실험을 닫은 목적은 달성; 매매효과는 주장하지 않는다.'
 elif title.startswith(('docs(', 'test(')) or sha in ['4fa9bc39','51d88738']:
  grade='no_effect';reason='문서/fixture/시험 격리의 직접 추가 Players는0. 실행 중인 판단 정책의 개선과 구별한다.'
 else:
  reason='방향성 수치만으로 가설 적중이라 판정할 수 없다. 다른 커밋·시장 수요·SKU/등급 구성과 겹친다.'
 if grade=='unclear':
  buyer=any(x in title.lower() for x in ['buyer','food pick','food opening','food shelf','food ladder'])
  metric='동종 주문100건의 지급률·성숙 Saved/주문이 대조 이상, 잘못된 지급0' if buyer else '동일 SKU×등급의 숙성40건에서 대조 대비 순기여/시간>0, 실패0'
  reason+=f' 결측: 해당 코드에 노출된 처리군/대조군과 성숙 결과. 이번 측정: 커밋시각 전후 원장·주문연결 환불 재집계. 판정: 11/10 01:30 KST까지 {metric}; 표본부족이면 미확정 종료, 신규 확대 보류, 그 실험의 기존 undo에 따라 해당 변경만 복원 검토(사람 스위치 유지).'
 grades.append(dict(change=f'{sha} {title}',grade=grade,reason=metrics+' '+reason+f' 근거 {A}:{i}'))
 keep.append(dict(item=sha+' '+', '.join(r['files']),decision='keep',reason='현재 HEAD 상태 유지. '+reason))
 for label in ['before','after']:
  cite(A,i,'"'+label+'": '+json.dumps(r[label],ensure_ascii=False),sha+' 정산/순기여 전후와 원본 원장 위치')
# Cycles include the failed previous Astra; every strategy check and carried gap is assessed.
cycles={}
for i,l in enumerate(open('meta/changes.jsonl'),1):
 r=json.loads(l)
 if r['cycle']>= '':
  if r['time']>='2026-11-09T01:18:16':cycles[r['cycle']]=(i,r)
for cycle,(i,r) in cycles.items():
 ans=r.get('answer') or {}; state=r['status']; reason='';grade='unclear'
 if cycle.startswith('astra'):
  grade='harmful';reason='이전 Astra는 실제 행에 없는 OK를 인용해 전체 변경이 거절됐다. 검토내용의 타당성과 전달실패를 분리해야 한다.'
 elif cycle.endswith('013953'):
  grade='worked';reason='잘못된 자동도구를 재현·삭제한 목적 달성. 실제 매매 개선은 아직 분리되지 않았다.'
 elif cycle.endswith(('025254','062902','074022')):
  grade='harmful';reason='측정 누락 또는 틀린 줄 인용으로 검토가 적용되지 않았다. 같은 원가/target 결측을 반복해 판정을 미룬 절차를 감점한다.'
 elif cycle.endswith('040408'):
  reason='두 창 판매를 실제 조회한 점은 유효. pricing 동시 변경을 포함한 거절이어서 Terra가 사람 설정을 바꿨다고 단정할 수 없다.'
 elif cycle.endswith(('085220','100754')):
  reason='맥락 철회 시도는 테스트 로더/실시간 파일 간섭으로 거절됐다. 실패를 이 프롬프트의 역효과로 돌리지 않는다. 누적 원가 결측 반복은 별도 개선 대상이다.'
 elif cycle.endswith('112131'):
  grade='worked';reason='실패한 수요맥락 실험을 실제 종료했다. 완결된 테스트 결과 전에 성공을 주장하지 않는 원칙은 더 강화해야 한다.'
 elif cycle.endswith('123606'):
  reason='총재고를 이유로 한 광범위 축소 오류를 정확히 발견했다. 0.7846s 대 0.75s 타이밍 시험으로 수정은 원복됐으며 아직 운영효과 없다.'
 else:reason='두 창 판매를 측정했으나 target 구속과 최근 숙성손익을 끝내 연결하지 못했다.'
 if cycle.startswith('terra'):
  reason+=' 최신 Patch 3.5 산식과 Players 목표는 인식했고 신규 패치는 없었다. Luna 상한 복구/총량 축소는 이번 기록에서 확인되지 않았다. 최신 cap절단0/0이므로 총상한 미인상은 감점하지 않는다. 6h/12h 경쟁속도 비교는 했으나 별도 전략의 추가효과는 미식별이라 현행 유지 자체는 타당하다.'
 if grade=='unclear':reason+=' 결측: 적용 후 독립 결과. 이번 측정: changes의 실패원인·전후 원장·최근 숙성창 조회. 판정: 11/10 01:30까지 재입고3판단 중 총량근거축소0/SKU근거2 이상; 미달은 신규맥락만 철회.'
 # Retain the original before/after numbers in the cycle's grade summary as evidence.
 original=ans.get('grade_summary') or ans.get('summary') or ans.get('change_summary') or ''
 grades.append(dict(change=cycle+' 규칙·전략·검증',grade=grade,reason=reason+f' 원본 수치/판정: meta/changes.jsonl:{i}. '+original[:850]))
 cite('meta/changes.jsonl',i,'"cycle": '+json.dumps(cycle),reason)
 keep.append(dict(item=cycle+' 전략/프롬프트/설정 판정',decision='keep',reason='실제로 적용된 HEAD 상태를 유지하고 거절된 변경은 성공으로 재등록하지 않는다. '+reason))
# Every current and historically recorded tool, including removed tools.
tools={r['name']:(i,r) for i,r,_ in rows if r['kind']=='tool'}
for name in sorted(set(tools)|set(p['tools'])|{d.name for d in Path('tools').iterdir() if d.is_dir() and d.name.startswith('auto_')}):
 calls=p['tools'].get(name,{}); live=Path('tools',name,'tool.py').exists();drop=name=='auto_market_brief'
 reason=f"팩 누계 {calls.get('calls',0)}호출/{calls.get('failed',0)}실패. "
 if name in tools:
  i,r=tools[name];reason+=f"해당12.21h 호출 {r['calls']}/실패 {r['failed']} ({A}:{i}). "
 if drop:reason+='rice 한 상품 검색에서 sofa도 함께 반환하는 fixture 실패. 이번에 tool.py와 manifest(selftest)를 제거했고 전용 test파일은 없었다. 원본은 dropped-tool.json에 보존했다.'
 elif not live:reason+='이미 없는 구현은 되살리지 않고 삭제 상태 유지. 빈 폴더/캐시는 사용 가능 도구가 아니다.'
 else:reason+='기존 구현 유지. 실패0을 의미 정확성/수익성으로 해석하지 않는다. 새로운 중복도구 생성은 보류하고 실제 사용과 유효 결제근거를 확인한다.'
 keep.append(dict(item='tools/'+name,decision='drop' if drop else 'keep',reason=reason))
# Concrete current evidence, exact text from one physical line.
for n in [1,2,3,4,5,6,14,19]:
 line=Path(P).read_text().splitlines()[n-1]
 cite(P,n,line[:min(len(line),650)],'이번 고정시각 측정: 도구 필터/공급창/최근 숙성 순기여')
for i,r,l in rows:
 if r['kind'] in ['method','projection','rival']:cite(A,i,l[:min(len(l),650)],'12시간 비교와 경쟁자 이동. 경쟁자 원가는 공개되지 않는다')
find(PACK,'"objective_matches": true','공식 산식과 점수표 일치')
find(PACK,'"players": 1.656','현재 Players')
find(PACK,'Reject the broad formula proposal: existing stock and carrying costs are high.','총재고 근거의 폭넓은 제안 거절')
# Raw evidence outside meta, as the harness requires for carried gaps.
for i,l in enumerate(open('.market/decisions.jsonl'),1):
 if 'existing stock and carrying costs are high' in l:
  cite('.market/decisions.jsonl',i,'existing stock and carrying costs are high','실제 재입고 판단에서 총량 근거 축소');break
recent=next(json.loads(l) for l in open(P) if json.loads(l).get('kind')=='recent_mature' and json.loads(l).get('sku')=='G8ec51935')
needle='"seq": '+str(recent['sale_seqs'][0])+','
for i,l in enumerate(open('.market/ledger_rows.jsonl'),1):
 if needle in l and '"k": "settle"' in l:
  cite('.market/ledger_rows.jsonl',i,l.rstrip(),'최근 G8 숙성창의 원본 결제. 순손익은 해당 코호트 전체 원가/환불 연결 결과');break
find('meta/evidence/'+C+'-rules.json','The forum rating counts as itself.','Patch 3.5 원값 forum을 확인')
grades.append(dict(change='tool_forge auto_market_brief',grade='harmful',reason='조회기간 호출0이라 실거래 효과는 미관측이다. rice 검색은 기대1개 SKU 대신 sofa까지 포함한 전체 사전을 반환했다. 도구 계약 위반을 재현해 이번에 구현과 selftest manifest를 제거했다. 근거 '+P+':1'))
keep.extend([
 dict(item='pricing.json / hard_limits.py / 사람의 거절 목록과 runtime 스위치',decision='keep',reason='총상한1B와 파산·현금 가드 유지. 최신 총상한 절단0/0이고 최근 G8 숙성손실이므로 일괄 확대 조건이 없다. Luna 호출 상한도 도입하지 않는다.'),
 dict(item='meta/strategy.md / meta/terra.md',decision='keep',reason='Patch3.5와 현재 경쟁속도로 전략을 갱신하고 반복 결측의 만료·실측 규칙을 강화했다.'),
 dict(item='meta/restock-sku-evidence.txt / meta/experiments.json 신규2개',decision='keep',reason='주 실험부터 Terra가 코드 적용과 함께 하나씩 시작한다. 독립 효과 미식별이므로 판정선 미달이면 임시 맥락만 철회한다.'),
 dict(item='완료된 기존 실험4개의 queue 항목',decision='drop',reason='모두 closed이므로 실제 큐에서 제거했다. 이전 큐 사본과 experiments_log.jsonl은 보존했다.')
])
options=[
 dict(direction='현행 규모 전략과 SKU별 재입고 판단 복구',expected_profit='기준 대비 추가 ΔPlayers는 미식별(기준0). 추가 book 5,480,141원으로 이동한 문턱을 넘을 때만 +.00847. 기존 추세12h +.02118 또는 +.00885와 구별한다.',risk='최근 숙성1498건 순기여 -2,839,568원이라 광범위 확대는 금지. 총량가드 유지하고 SKU별 손실/잔류를 분리한다.',test='재입고3판단에서 총재고만으로 축소0, SKU근거 완비2 이상; 12h 후 철회/유지'),
 dict(direction='고가 총량 일괄 확대',expected_profit='현1B cap절단0/0이므로 cap 인상 효과0. 추가 book·매출 문턱 동시 통과 시에만 +.01355이며 실현 기대값은 미식별.',risk='서지·환불·보관비 증가. G8 최근116숙성 -3,992,702원. 총량축소 대신 해당 SKU 판단.',test='두 창 target 구속·60만+ 추가분 판매·숙성흑자와6h book 비하락 검증'),
 dict(direction='구매 Saved/포럼 우선 전환',expected_profit='추가 Saved .0007로 이동한 문턱 통과 시 +.00893, forum +.001이면 +.0003. 현재 정책 외 추가분인지 미식별. 경쟁자3h/12h 외삽 후 격차 .05635/.08974에 적용.',risk='진행중인 사람의 A/B와 중첩, 협상 지연·지급률 하락·미보증 파손.',test='동종100주문의 지급률과 성숙 Saved 대조; 스위치 유지')]
gap=dict(missing='재입고 맥락 교정의 독립 운영효과와 G8 최근 손실의 등급별 원인. 누적 원가1건 결측과 최근창 결측은 별개다.',measurement='이번에 두 완료창과 guard절단0/0, 실제 거절 판단, 최근 G8 숙성116건/원가누락0/순기여 -3,992,702원을 원장에서 재집계했다.',judge_line=dict(metric='총재고만을 근거로 축소한 판단 수 및 SKU근거 완비 판단 수',direction='below',threshold='총량근거 축소1건 미만, SKU근거 완비2건 이상',min_sample='재입고 판단3건',expires='2026-11-10 01:30 KST',undo='restock.GOAL에 이번에 추가한 세 문장만 제거; 가드·수량·사람 스위치 유지'))
report=dict(changed=True,hypothesis='총재고 원가에 반응하는 일괄 회피를 SKU별 근거 판단으로 바꾸면 규모 전략을 유지하면서 실제 손실 품목만 구별할 수 있다.',change_summary='재입고 맥락 세 문장을 준비해 Terra 순차 실험 큐에 등록, 잘못된 auto_market_brief 제거, Terra 판정 종료 규칙과 전략·직렬 실험 큐·REPORT 갱신. 60커밋과11검토 및 새 자동도구를 개별 평가했다.',target_metric='Everyone Players 순위; 선행지표 재입고3판단 중 근거없는 총량축소0/SKU근거2 이상',expected='12h 추가 ΔPlayers는 아직 미식별. 확대나 인하 없이 판단 오류0을 목표로 검증한다.',evidence=evidence,evidence_gap=gap,summary='현재1.656·4/63위. 공식 Patch3.5 일치. 최근 G8 숙성116건 순기여 -3,992,702원은 누적흑자와 다르다. 총량은 유지하고 SKU근거를 교정한다.',strategy=dict(options=options,chosen='현재 규모 전략 유지. 다른 대안이 같은12h에서 추가 Players를 더 만든다는 근거가 없으며, SKU별 판단 복구를 먼저 시험한다.',experiment='sku-evidence-restock-133041을 먼저 실행; 11/10 01:30에 3판단/총량축소0/SKU근거2로 판정. 그 뒤 recent-cohort-context-133041을12h 순차 실행.'),grades=grades,keep_or_drop=keep)
result=Path(f'meta/evidence/{C}-test-result-final.json')
if result.exists():
 result_data=json.loads(result.read_text())
 report['summary']+=' 최종 격리검증 종료코드 '+str(result_data['returncode'])+'.'
 test_path=f'meta/evidence/{C}-tests-final.txt'
 for n,line in enumerate(Path(test_path).read_text().splitlines(),1):
  if line.startswith('Ran ') or line in ['OK'] or line.startswith('FAILED ('):
   cite(test_path,n,line,'지정 명령 최종 격리검증 실제 요약')
Path(f'meta/evidence/{C}-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=1)+'\n')
print('grades',len(grades),'keeps',len(keep),'evidence',len(evidence))
