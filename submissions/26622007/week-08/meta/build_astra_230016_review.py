"""Build the complete cycle review with exact, verified evidence excerpts."""
import json,sys,datetime,statistics,collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
C='astra-1106-230016'; A=f'meta/evidence/{C}-audit-v2.jsonl';OUT=f'meta/evidence/{C}-review.json'
rows=[json.loads(l) for l in (ROOT/A).read_text().splitlines()]
evidence=[]
def ev(path,line,meaning):
 text=(ROOT/path).read_text().splitlines()[line-1]
 evidence.append({'ref':f'{path}:{line}','excerpt':text,'meaning':meaning});return f'{path}:{line}'
def find(path,needle,meaning):
 for i,l in enumerate((ROOT/path).read_text().splitlines(),1):
  if needle in l:return ev(path,i,meaning)
 raise ValueError((path,needle))
grades=[];keep=[]
for i,r in enumerate(rows,1):
 if r['kind'] not in ('commit','terra','tool'):continue
 ref=ev(A,i,'고정 마감 원장 감사: 시장 전체 거래와 미숙성·원가 누락을 함께 표시한다.')
 if r['kind']=='commit':
  b,a=r['before'],r['after'];sha=r['sha']; grade='unclear'
  numbers=f"판매 {b['paid']}→{a['paid']}, 환불후 기여 {b['net_after_refunds']}→{a['net_after_refunds']}원, 시장 거래 {b['market_paid']}→{a['market_paid']}."
  reason=f"{numbers} {ref}."
  if r['title'].startswith(('chore','test')):
   grade='no_effect';reason+=' 기록·검증 자체의 거래 효과를 분리하지 않는다.'
  elif sha in ['16e9c09','a0f52ed']:
   grade='harmful' if sha=='16e9c09' else 'no_effect';reason+=' Astra 제한시간 초과로 지속 변경이 남지 않았다.'
  elif sha=='22446ad':
   grade='harmful';reason+=' 거래 코드와 무관한 harness_failed +16으로 올바른 산식을 되돌렸다. 이후 9424b13이 복구했다.'
  elif sha=='3f736ed':
   grade='worked';reason+=' SKU/정산 검색 회귀 검증과 이후 423호출·실패0으로 기능 복구 확인. 순위 인과효과는 미확정.'
  elif sha in ['66a241c','9424b13']:
   grade='worked';reason+=' 디스크 산식은 현재 보드 drift 0을 재현. 운영 프로세스 drift -0.009는 별도 미해결이다.'
  else:
   reason+=' 동시 가격·공급·모델 변경과 짧은 구간이 교란한다. 빠진 것은 동일 조건 숙성 전후 표본이다. 이번에 전후30분 원장을 재계산했다. 판정: 11/07 11:00 KST, 동일 SKU·등급 숙성20건씩의 재고시간당 순기여가 이전 이상; 미달 시 해당 커밋의 명시 undo만 검토하고 총량은 줄이지 않는다.'
  grades.append({'change':sha+' '+r['title'],'grade':grade,'reason':reason})
  keep.append({'item':sha,'decision':'keep','reason':'현재 후속 수정본과 원본 이력을 보존한다. '+('기록만 존재하며 경제적 설정은 없다.' if grade=='no_effect' else '독립 철회조건 충족이 입증되지 않았거나 이미 후속 커밋으로 교체됐다. 보호 파일은 편집하지 않는다.')})
 elif r['kind']=='terra':
  b,a=r['before'],r['after'];cid=r['cycle']; grade='unclear'
  reason=f"판매 {b['paid']}→{a['paid']}, 환불후 기여 {b['net_after_refunds']}→{a['net_after_refunds']}원. {ref}. "
  if cid.endswith('132617'):
   grade='worked';reason+='검색 결함을 실제 수정했다. 패치·전체 규모 전략은 유지하되 경제적 효과는 별도 표본이 필요하다.'
  else:
   grade='harmful';reason+='패치 인지는 했지만 실행 프로세스 갱신을 완료하지 못하고 같은 판정선을 반복했다. '
   if cid.endswith(('110118','121452','143855')):reason+='상한/수량 차단·회전을 계산하지 않은 채 규모 확대를 미뤘다. 세 조건 충족을 이 감사가 소급 입증하지는 않으므로 잘못된 미확인을 감점한다.'
   if cid.endswith('170159'):reason+='watch는 적절하나 이전보다 뒤진 성장의 구체적 전환 측정은 부족했다.'
   if cid.endswith(('181325','192712')):reason+='Patch 3.5를 당일 인지·수정한 판단은 옳다. 자동 원복/재시작 미완료는 시스템의 실패다.'
  grades.append({'change':cid+' 전략 점검·패치 대응','grade':grade,'reason':reason})
 elif r['kind']=='tool':
  drop=r['name']=='auto_market_research_bundle'
  keep.append({'item':'tools/'+r['name'],'decision':'drop' if drop else 'keep','reason':f"이번 구간 {r['calls']}호출·{r['failed']}실패. {ref}. "+('실제 필드 fixture에서 상품0·정산0, 검증된 scan은1·1. manifest/tool.py를 삭제했다. 전용 테스트는 없으며 원본은 dropped-tool.json에 보존.' if drop else '오류·회귀 근거 없이 제거하지 않는다. 호출0은 무효 판정이 아니며 분석용 도구의 중복도 감시한다.')})
# The second audit runs after deletion; retain the original tool decision.
if not any(x['item']=='tools/auto_market_research_bundle' for x in keep):
 ref=find(f'meta/evidence/{C}-audit.jsonl','"kind": "tool", "name": "auto_market_research_bundle"','삭제 전 도구83호출·실패0; 의미 검증은 실패했다.')
 keep.append({'item':'tools/auto_market_research_bundle','decision':'drop','reason':f'{ref}. SKU catalog와 k=settle을 못 읽어 fixture 상품0·정산0. manifest/tool.py 삭제 완료, 전용 테스트 없음, 원본은 dropped-tool.json 보존.'})
# Add direct sources and primary observations.
find(f'meta/evidence/{C}.json','The forum rating counts as itself','Patch 3.5: 포럼 평점 원값과 Saved를 사용한다.')
find(f'meta/evidence/{C}.json','Buyers score on what they save.','Patch 3.4: 100건의 0% 가상 거래를 포함한 Saved 비율이다.')
find(f'meta/evidence/{C}.json','Buy more votes on one buyer.','유료 비추천 제곱 비용은 book을 낮춘다.')
find(f'meta/evidence/{C}.json','"buyers": -0.009','현재 운영 팩은 이전 산식이다.')
find(f'meta/evidence/{C}-board-check.json','"buyers": 0.0','현재 디스크 코드로 같은 보드를 계산하면 오차가 없다.')
find(A,'"kind": "cycle"','검토 기간 전체 판매와 숙성 손익, 원가 누락 수.')
find(A,'"kind": "rival", "name": "inextro"','고가 구성·유입 비교; 경쟁 원가는 알 수 없다.')
find(A,'"kind": "rival", "name": "moss-and-oak"','식품과 고가 상품을 함께 회전하는 경쟁 전략.')
find(A,'"kind": "cohort", "label": "review", "sku": "G1515d951"','35건 숙성 기여 2,417,097원, 추가 가격 실험 기준.')
find(A,'"kind": "cohort", "label": "g6_trial"','G6 A 숙성7건으로 최소20건에 못 미친다.')
find('logs/market-kit.log','22:03:27] seller PRICE G1515d951','희소가 380911이 현재 첫 제시가를 높이는 실제 로그.')
find(f'meta/evidence/{C}-dropped-tool.json','"deal_count": 0','잘못된 통합 도구 fixture는 정산을 놓친다.')
find(f'meta/evidence/{C}-dropped-tool.json','"matched_paid": 1','검증된 대체 도구는 같은 정산을 찾는다.')
find(f'meta/evidence/{C}-audit.jsonl','"id": 25130','해조류 미충족 수요는 자기주장이고 거래로 추가 검증한다.')
find(f'meta/evidence/{C}-explore.jsonl','"sku": "G8074a394"','최근6시간 37판매·14구매자·중앙11700, 최악원가20138.')
find(f'meta/evidence/{C}-explore.jsonl','"window": "recent"','구매 67주문31지급·중앙45.92초, 거래 구조 개선의 잠정 관측.')
find(f'meta/evidence/{C}-tests-final.txt','Ran 539 tests','전체539테스트 통과. 첫 실패 출력도 보존한다.')
options=[{'direction':'현행 고가 검증 재고 확대와 원가·보증 하한 내 경쟁가격','expected_profit':'다음12시간 book +1,200만~6,000만원 시나리오. 숙성 시간당 약120만원과 최근4시간 잠정 기여 2,457만, 추가 환불·보관료를 감안한 범위이며 보장은 아니다.','risk':'최근517판매 미숙성, 원가4건 누락. 서지와 환불로 가격 인하 효과가 상쇄될 수 있다. 기존 총량·거절정책·파산 완충 유지.','test':'진행 중 G6 시험을 먼저 종료한 뒤 G1515 희소 호가만 3% 낮춰 동일 등급·재고시간당 숙성 순기여를 비교한다.'},{'direction':'고가보다 저가 식품·구매 Saved·포럼 수요에 집중','expected_profit':'기존 숙성 시간당 약120만원을12시간 외삽하면 약1,400만원. 해조류 1개 시험은 원가를 정가60%로 가정할 때 +960원이며 실제 원가 미확인이다.','risk':'가게 book 선두 격차5.40억원을 줄일 규모가 부족하다. 식품 폐기 위험과 공개 요청의 미전환 가능성.','test':'G8074a394 한 단위, 실제 원가·유통기한·2공급창내 지급 여부를 확인; 본 전략 전환 대신 작은 후순위 탐색.'},{'direction':'선두 고가 SKU에 더 집중하고 상한을 즉시 추가 확대','expected_profit':'inextro 12.75시간 매출13.19억원 대 우리2.29억원이 상방 수요를 보이지만 경쟁 원가가 없어 순이익 증가액 추정 불가.','risk':'현재 재고1.00억원·목표 총상한10억원이라 상한이 병목이라는 증거가 없다. 미숙성 가격 변경과 동시 확대는 원인 분리를 막는다.','test':'두 공급창 우선줄 차단·해당unit 판매·숙성 흑자/book 비감소를 확인한 SKU만 다음 한 단계 검토.'}]
answer={'changed':True,'hypothesis':'패치 의미와 실행 코드 버전을 함께 검증하고 빈 결과를 반환하는 도구를 제거하면 잘못된 목표와 증거로 전략을 바꾸는 오류를 줄일 수 있다.','change_summary':'Patch 3.4/3.5에 맞춰 전략·Terra 지침을 재작성하고, 포럼 점수 방식 검사·사이클 경계 재실행·현행 보호한도 재로딩·오프라인 수정의 canary 분리를 추가했다. 무효 검색 도구를 삭제하고 실험 두 개를 대기열에 추가했다.','target_metric':'구매자 board_drift, 잘못된 자동 원복, 재고시간당 숙성 순기여, Everyone Players 및 book 순위','expected':'다음 새 프로세스 팩의 drift 절댓값 <0.0005, 오프라인 수정의 무관한 거래오류 원복0건, 잘못된 통합검색 노출0건. 가격 실험은 숙성20건 후 이전 순기여/재고시간 이상이어야 유지한다.','summary':'23:00 공식 Players 1.471(15위), book 135,899,583원(14위), book 선두와540,091,173원 차이다. 원장1175판매 중517건 미숙성이므로 최근 확대의 성공을 단정하지 않았다. Terra는 패치를 인지했지만 실행 갱신과 반복된 증거 부족 해소에 실패했다. 기존 고가 규모 전략을 유지한다.','strategy':{'options':options,'chosen':'현행 고가 검증 재고·경쟁가격 전략 유지. 다른 방향이 book·Players에서 더 낫다는 숙성 근거가 없으며 전체 재고를 축소하지 않는다.','experiment':'G6 실행분은11/07 04:39 판정(현재 A숙성7/20). 뒤이어 G1515 희소가380911→369484만12시간 시험, 숙성20건·같은 등급의 재고시간당 순기여가 기준 이상이면 유지. 후순위 G8074a394 한 단위는2공급창내 지급·최종 순기여>0이면 성공.'},'grades':grades,'keep_or_drop':keep,'evidence':evidence,'evidence_gap':{'missing':'05:22 시작한 운영 PID에는 이번 재실행 코드가 아직 없다. 현재 팩은 buyer drift -0.009이고 디스크 재계산만0이다. 최근 고가 확대517판매의6시간 환불 결과와4건 원가도 부족하다.','measurement':'현재 디스크로 공개 보드를 재계산하고 운영 팩과 대조했다. 전체 원장을 고정 마감으로 중복제거·주문연결 환불 재집계하고 전후30분·시간별·SKU숙성 결과를 저장했다.','judge_line':{'metric':'새 운영 프로세스 첫 팩의 구매자 board_drift 절댓값','direction':'below','threshold':'0.0005','min_sample':'코드 갱신 후 운영 팩1개; 현재 디스크 재계산은 대체 불가','expires':'2026-11-07 01:00 KST','undo':'미달이면 산식을 되돌리지 말고 완료된 사이클 경계에서 감독 프로세스의 재시작을 적용한다. 이미 시작한 구버전은 자동 갱신되지 않는다.'}}}
buyer_path=f'meta/evidence/{C}-buyer-commits.jsonl'
for i,line in enumerate((ROOT/buyer_path).read_text().splitlines(),1):
 r=json.loads(line)
 g=next((x for x in grades if x['change'].startswith(r['sha']+' ')),None)
 if g and any(word in g['change'].lower() for word in ['buyer','바이어','quote','선택']):
  ref=ev(buyer_path,i,'커밋 전후 실제 구매 지급과 주문 처리시간; 동일 조건 실험은 아니다.')
  b,z=r['before'],r['after']
  g['reason']+=f" 구매 지급 {b['paid']}→{z['paid']}, 주문 중앙초 {b['median_seconds']}→{z['median_seconds']} ({ref}). 구매 변경 판정은 동일 카테고리100주문씩의 실제 Saved 비감소·지급/시간 증가를11/07 11:00에 확인한다."
(ROOT/OUT).write_text(json.dumps(answer,ensure_ascii=False,indent=1))
print('grades',len(grades),'decisions',len(keep),'evidence',len(evidence))
if __name__=='__main__':
 import meta_harness as mh
 problems=mh.verify_evidence(evidence,root=str(ROOT));print('evidence problems',problems)
 if problems:sys.exit(1)
