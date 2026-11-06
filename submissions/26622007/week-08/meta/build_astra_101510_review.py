"""Build review from immutable cycle evidence and validate every cited line."""
import json
from pathlib import Path
BASE='meta/evidence/astra-1106-101510'
def main():
 audit=[json.loads(l) for l in Path(BASE+'-audit-v2.jsonl').read_text().splitlines()]
 evidence=[];grades=[];keep=[]
 def ev(path,n,meaning,excerpt=None):
  line=Path(path).read_text().splitlines()[n-1]
  evidence.append({'ref':f'{path}:{n}','excerpt':excerpt or line,'meaning':meaning});return f'{path}:{n}'
 def decision(item,reason,value='keep'):keep.append({'item':item,'reason':reason,'decision':value})
 comments={
 '9ade427':('unclear','직전 Astra의 복원·측정·도구가 10분 뒤 전부 되돌려졌다. 거절10/11건과 변경의 인과 연결이 결측이다. 이번 원장 전후를 조회했다. 다음 두10분 창에서 변경 경로 자체의 회귀오류0이면 교정 유지, 발생 시 해당 경로만 복원한다.'),
 '6850a97':('unclear','체크아웃+10·공급거절+11의 원인 귀속 없이 전체 복원했다. 경제적 실패 여부는 결측이다. 이번 원장·실패로그를 읽었다. 다음 두10분 창의 동일 요청 오류율이 기준보다 높고 변경 경로와 연결되면 해당 변경만 철회한다.'),
 '068d24c':('worked','단위 입고·실제지급·관측출고를 분리하는 조회가 복구됐다. 이번 3공급창에서도 동작했고 빈 매입표본을 결측으로 남겼다. 직접 매출효과는 아니다.'),
 'de87e31':('worked','리뷰 대가 약속을 제거하고 회전 로그·canary 구간 집계 회귀시험을 유지했다. 코드를 통한 측정 교정 성과이며 매출 증가의 인과효과는 아니다.'),
 '5c19b0f':('harmful','손실 없는 G052 잔류만으로 축소했고 테스트 존재하지 않는629행 인용으로 거절됐다. 최근 숙성10건+1,020,285원이며 목표3 유지. 피해매출액은 입증되지 않았다.'),
 'faa416b':('no_effect','거절된 축소를 즉시 되돌린 기록이다. 검증실패와 실제 운영 손실을 동일시하지 않는다.'),
 'a84f87c':('harmful','G052의 숙성손실 없이3→2를 재시도했다. 최근10건+1,020,285원으로 축소근거가 없다. 자동복원으로 현재3이며 재적용하지 않는다.'),
 'a018ba9':('unclear','잡음 Traceback24개로 복원되어 회귀판정 근거가 잘못됐다. 실제 코드오류는 결측이다. 이번 로그 분류와 전체시험을 재검증했고 다음 두 canary창 독립 오류0이면 분류유지, 실제오류를 가리면 필터철회한다.'),
 '82f15da':('unclear','식품즉시수락 효과가 이후75%/70% 정책과 혼재한다. 변경만의 지급률·공식 saved 효과가 결측이다. 이번 주문/지급을 연결했다. 다음 동카테고리100주문 두 코호트의 saved기여/주문20% 하락시 수락경로 재검토, 11/06 22:15 판정.'),
 'dc91249':('harmful','G003을 숙성손실 없이2→1로 축소했다. 최근10건+5,295,948원이다. 실패기록은 보존하되 축소정책은 폐기한다.'),
 'e662e66':('unclear','Traceback24개가 a2a 정리잡음으로 밝혀져 자동복원의 오류귀속이 불확실하다. 로그·시험을 이번 재검증했다. 두 canary창 독립오류0 유지, 실제오류를 가리면 필터철회한다.'),
 '470cecb':('harmful','G003 잔류만으로2→1을 수락시켰다. 최근 숙성10건 순기여+5,295,948원. 이번2로 복원하고 관련 설정시험도 수정했다. 놓친 판매액은 미확정이다.'),
 '22cc0d4':('unclear','G6 A 변경 전20건+1,098,330원, 변경후3건 잠정+229,696원이나 숙성0건이다. 숙성표본이 결측이다. 이번 A가용시간17.1658→4.1191h와 기여율63983.78→잠정55764.14를 조회했고11/07 04:39 숙성20건·노출시간당 순기여≥기준일때만343000 유지, 아니면350000 복원.'),
 'd7fcd2e':('worked','기존10분 창 Traceback24→독립오류0/정리잡음24 분리가 시험으로 검증됐다. 다른 오류·불완전 블록은 계속 실패로 센다. 수익효과와 분리한다.'),
 '24935ce':('worked','정리잡음은 정확히2개 Traceback 블록만 허용하고 세 번째·중첩오류는 실패로 세는 회귀검증을 유지한다.'),
 '7fde369':('worked','취소된 견적 대화를 재사용하지 않는 경로와 불완전 detach 블록을 잡음으로 빼지 않는 회귀검증을 통과했다. 직접 지급증가 인과는 미확정이다.'),
 '605b100':('worked','Patch3.4 saved를 Terra가 발견해 수정했다. 새 보드 상점55/구매자51행 오차0을 재현했다. 다만 기존 프로세스의 이번 팩 drift .110은 해결되지 않아 배포성과와 분리해 감점한다.'),
 '04817f8':('unclear','75%가격한도·70%식품수락·saved견적·좋아요만·별점요청삭제가 함께 바뀌었다. 지급125/145→42/97, 명목절감/지급 .1925→.3098이나 공식 saved와 주문구성 통제가 결측이다. 이번 지급연결을 했고11/06 22:15 동카테고리100주문 두 코호트 기여/주문20% 하락시 수정안, 사람설정 임의철회 금지.'),
 '79df539':('unclear','새 프로세스 reload 회귀시험은 통과했지만 이미 실행 중인 하네스에는 반영되지 않아 팩 drift .110이다. 새 프로세스 팩이 결측이다. 이번 새모듈 보드재현은0이며11/06 11:30 새팩1개 drift≤.0005여야 통과, 실패시 산식이 아닌 실행경로 수정.')}
 for i,r in enumerate(audit,1):
  if r['kind']!='commit':continue
  b,a=r['before'],r['after'];sha=r['sha']
  g,why=comments.get(sha,('no_effect','하네스 실행·수락/실패 기록이며 독립 운영 변경은 없다. 원본 이력은 보존한다.'))
  excerpt=json.dumps(r,ensure_ascii=False).split('"before": ',1)[1]
  ref=ev(BASE+'-audit-v2.jsonl',i,sha+' 전후30분 지급·원가확인 환불차감, 시장판매 및 미숙성 포함',excerpt)
  reason=f"지급 {b['paid']}→{a['paid']}, 원가확인 환불차감 {b['net_after_linked_refunds']:,}→{a['net_after_linked_refunds']:,}원 ({ref}). {why} 시장수요·동시수정·보증미숙성으로 단독 인과효과는 아니다."
  grades.append({'change':sha,'grade':g,'reason':reason})
  files=[p.split('week-08/')[-1] for p in r['files'] if '/evidence/' not in p and not p.endswith('.jsonl')]
  value='drop' if sha in ['470cecb'] else 'keep'
  detail=('G003 목표1을2로 실제복원, 설정테스트 동반수정.' if value=='drop' else
          '실패·복원 이력 보존, 되돌려진 동작은 재적용하지 않는다.' if sha in ['9ade427','5c19b0f','a84f87c','dc91249'] else
          '현재 유효한 구현·방어·기록은 유지한다. '+why)
  decision(sha+' '+', '.join(files),detail,value)
 review_notes={
 'astra-1105-214905':('unclear','이전61커밋·10Terra 등급은 changes:42에 남아 있지만 변경은 canary로 전부복원됐다. 자체 실험의 인과효과가 결측이다. 이번 전후원장을 재측정,11/06 22:15 복원SKU별 숙성5건 양수일때 유지하며 손실10건때만 축소한다.'),
 'terra-1105-222640':('worked','읽기전용 회전측정을 복구하고 실제지급과 출고를 구분했다. 이번 공급창 재조회로 계속 동작함을 확인했다.'),
 'terra-1105-233823':('harmful','존재하지 않는 테스트행 인용으로 수락실패. 숙성손실 없는 G052축소도 사용자 지침에 어긋났다.'),
 'terra-1106-005055':('harmful','G052 흑자축소 재시도. canary 정리잡음은 별도 문제이며 이 축소의 근거를 정당화하지 않는다.'),
 'terra-1106-020248':('harmful','G003 흑자축소 재시도. 총상한 유지 자체는 확대조건 미달로 타당하다.'),
 'terra-1106-031450':('harmful','G003 잔류만으로 축소를 수락시켰다. 카펫1건+51,233원(보관료 전) 성공판정은 타당하나 축소는 이번 폐기했다.'),
 'terra-1106-042645':('unclear','G6 실험 시작은 대기열 순서와2%폭을 지켰다. 숙성효과는 결측이다. 이번 A3건·숙성0을 조회,11/07 04:39 A숙성20건 노출시간당기여 비하락일때 유지, 미달350000복원.'),
 'terra-1106-053911':('unclear','보증숙성을 기다린 것은 타당하다. G6 독립효과와 숙성표본이 결측이다. 이번 등급별 판매·가용시간17.1658→4.1191h 조회 후11/07 04:39 숙성20건 시간당기여 비하락 판정, 미달350000복원.'),
 'terra-1106-065025':('worked','Patch3.4를 발견해 saved 및 작성자별투표·비추천비용을 설명하고 산식/테스트를 수정했다. 운영프로세스 반영 누락은 다음 검토의 별도 문제다.'),
 'terra-1106-080228':('harmful','규칙은 설명했으나 잘못된 행 인용으로 무변경이 거절됐고 기존 프로세스 drift .090을 실시간 별도측정0과 혼동할 위험을 남겼다.'),
 'terra-1106-091334':('unclear','모듈갱신과 아직실행중인 프로세스 미반영을 정직하게 구분했다. 재시작팩 결측. 이번 새모듈 106행 재현을 측정,11/06 11:30 새팩1개 drift≤.0005면 통과, 미달 실행경로 수정.')}
 for i,r in enumerate(audit,1):
  if r['kind']!='review':continue
  g,why=review_notes[r['cycle']];b,a=r['before'],r['after']
  ref=ev(BASE+'-audit-v2.jsonl',i,r['cycle']+' 전략점검과 전후1시간',json.dumps(r,ensure_ascii=False).split('"before": ',1)[1])
  grades.append({'change':r['cycle']+' 전략·등급','grade':g,'reason':f"지급 {b['paid']}→{a['paid']}, 환불차감 {b['net_after_linked_refunds']}→{a['net_after_linked_refunds']} ({ref}). {why} 호출상한 재도입은 관측되지 않았다. 확대조건은 별도 scale-cycles 감사의 창별값으로 판단한다."})
 for i,r in enumerate(audit,1):
  if r['kind']!='tool':continue
  name=r['name'];ev(BASE+'-audit-v2.jsonl',i,name+' 사용·실패·selftest')
  why=f"직전검토 이후 {r['calls']}호출·실패{r['failed']}. "
  if name=='auto_auto_market_brief':decision('tools/'+name+' 및 test_auto_auto_market_brief.py',why+'scan과 구현이 동일하여 세 파일 삭제; 원본은 dropped-tool.json 보존.','drop')
  else:decision('tools/'+name,why+('SKU키 소실로 잘못된 빈결과를 반환하던 코드를 수리, JSONL 개인지급과 settle 연결·sandbox 회귀검증.' if name=='auto_market_research' else '읽기전용 기능 유지. 무호출 도구의 이익효과는 입증되지 않았으므로 확대하지 않는다.'))
 grades.append({'change':'tool_forge auto_market_research','grade':'harmful','reason':'31호출 실패0이지만 fixture 카탈로그/정산/개인지급 모두0이었다. 수정후 각각1. 실제 매트리스검색 상품128·정산28639. 검색결측을 시장수요 부재처럼 주는 의미오류를 고쳤다. 직접 손익손실은 미확정.'})
 grades.append({'change':'fc36f9c G03 축소 재검토','grade':'harmful','reason':'이전 Astra 복원이 자동취소되어 목표1이 남았다. 이번 최근숙성10건+3,071,283원, 결측원가0으로 손실축소 조건이 아니다. 목표2로 실제복원했다.'})
 decision('fc36f9c G03 목표1','최근숙성10건+3,071,283원. pricing.json 목표2 및 설정시험으로 복원.','drop')
 decision('meta_harness.score_comparable / scorecard.scoring_signature','Patch3.4·stale drift·알 수 없는 과거 집계 비교를 표시. 기존불변 로그는 변경하지 않았다.')
 decision('demand_inventory + restock 선택적 c3 + 테스트','미충족 수요글과카탈로그/실재고 연결을 읽기전용으로 제공. 기본꺼짐, G6 실험 이후 단독켜기.')
 decision('meta 전략·Terra·Astra 지침·실험큐','saved와 사람의 숙성손실 축소규칙으로 정리. 완료 카펫은 대기열에서 제거하고 완료로그는 보존.')
 decision('hard_limits·seller 거절·Luna 무상한·파산/보증/sandbox','보호파일과 사람설정은 그대로 유지. 총상한15M의 확대조건은 현재 차단0/0·신규매입0으로 미달.')
 for i in range(1,11):ev(BASE+'-measurement.jsonl',i,'공개규칙·보드재현·실제검색·한도·공급창·새수요 연결 감사')
 for i in range(1,5):ev(BASE+'-cohorts.jsonl',i,'원장연결 SKU 숙성손익·G6 등급별 전후')
 ev(BASE+'-cohort-exposure.jsonl',5,'G6 A등급 가용시간 합집합과 시간당 기여; 후구간 미숙성')
 for i,line in enumerate(Path(BASE+'-scale-cycles.jsonl').read_text().splitlines(),1):ev(BASE+'-scale-cycles.jsonl',i,'Terra별 공급창 확대조건 재측정')
 ev(BASE+'-audit-v2.jsonl',54,'직전검토 대비 동일길이 시장판매량과 자체판매량')
 for i,r in enumerate(audit,1):
  if r['kind'] in ['rival','forum_demand']:ev(BASE+'-audit-v2.jsonl',i,'경쟁상품·구매자·단가 / 미검증 공개수요')
 for fn in ['-tests-1.txt','-tests-2.txt','-tests-3.txt']:
  for i,line in enumerate(Path(BASE+fn).read_text().splitlines(),1):
   if line.startswith('Ran ') or line.startswith('FAILED'):ev(BASE+fn,i,'원본 시험결과; 첫 실패도 보존')
 options=[{'direction':'규모 전략 유지·G03/G003 흑자축소 교정·saved 관측','expected_profit':'12시간 순기여2,000만~3,000만원 시나리오. 직전12시간26분1,293판매·원가확인28,054,126원, 숙성668건14,890,670원. 복원 추가0~326만원은 각0~4건×301,684/513,566원 가정.','risk':'621건 미숙성·7건 원가결측·보관료 제외. 복원재고의 환불/보관료와 G6 공급간섭.','test':'G03/G0031→2 교정, 총상한15M유지. 주 G6실험을 끝낸 뒤 수요문맥 실험만 켠다.'},
 {'direction':'총상한30M 이상으로 즉시 고가 집중 확대','expected_profit':'현행흐름에0~393만원은 소파0~10건×숙성392,876원 가정. inextro1,374판매·8.39억원 매출 대비 우리1,293·1.30억원.','risk':'차단0/0·신규입고0으로 확대조건 미달. 경쟁원가 결측, 환불·보관료 위험.','test':'매입확대 대신 다음 두창 차단·실제지급·숙성흑자·book비하락 측정.'},
 {'direction':'구매자 saved·포럼 추가 집중','expected_profit':'상점 직접 추가이익0. 명목절감/지급 .1925→.3098이나 지급125/145→42/97, 절감/주문 .1660→.1342. 공식 saved 개선은 미확정.','risk':'거래급감·식품가치/파손결측·동시패치; 비추천은 현금손실.','test':'현75%/70%가격정책·좋아요 유지, 같은카테고리100주문씩 두코호트 관측.'}]
 strategy={'options':options,'chosen':'대안의 수익우위와 확대조건이 입증되지 않아 현행 규모 전략을 유지한다. 손실 없는 SKU축소는 복원하고 새 saved 기준으로 구매효과를 분리평가한다.','experiment':'G6 A343000원 실험 유지:11/07 04:39 숙성20건·같은등급 재고노출시간당 순기여≥전20건이면유지, 미달350000복원. 이후 forum_demand_context를12시간만 켜서6결정 중 정확한post_id+SKU인용≥1·오연결0, 미달false복원. G03/G003복원은11/06 22:15 SKU별숙성5건 조회, 표본미달 추가확대보류·최근숙성10건 적자일때만축소.'}
 gap={'missing':'기존 장기 실행 하네스의 새 프로세스 팩, G6 A숙성20건의 최종 순기여, 복원SKU 독립효과.','measurement':'새모듈로 공개보드55상점/51구매자 전행재현0오차. G6 전20건+1098330·후3건잠정229696·숙성0, 가용시간17.1658→4.1191h와 기여율63983.78→잠정55764.14를 단위연결하고 공급창차단0/0·신규매입0을 조회했다.','judge_line':{'metric':'새 프로세스 팩 buyer board_drift 절댓값','direction':'below','threshold':'0.0005 이하','min_sample':'새 프로세스 팩1개','expires':'2026-11-06 11:30 KST','undo':'차이가 남으면 구 surplus식으로 복원하지 않고 점수표 실행경로 수정; 해당 팩의 성장률 기반 전략판정 보류'}}
 result={'changed':True,'hypothesis':'흑자 SKU의 잘못된 축소를 복원하고 검색의 거짓 결측·교차패치 점수비교를 고치면 규모 전략의 판단근거와 판매기회를 개선할 수 있다.','change_summary':'G03/G003 목표1→2, auto_market_research 수리, 중복 auto_auto_market_brief와 전용시험 삭제. 점수 비교가능성 표시와 읽기전용 demand_inventory·꺼진 문맥경로 추가. 전략/지침/실험큐/보고서 갱신.','target_metric':'Everyone Players·book, SKU숙성순기여, board_drift, 검색정확성, 포럼요청 연결 인용','expected':'보드 새모듈106행 오차0, 검색fixture0→1. 복원 추가0~326만원은 시나리오이며 G6숙성0으로 아직 가격효과 미확정. 새수요도구의 직접이익은0으로 두고 인용효용부터 시험한다. 지정 venv 전체458테스트 통과.','evidence':evidence,'evidence_gap':gap,'summary':'실제 Players1.308(17/58), book102,366,064원; 팩1.418은 구식산식 오류. 30커밋·11검토·생성도구 및 이전G03축소를 전수평가했다. 최종458테스트 통과(앞선 두실패 원본 보존). 커밋·push·PR없음. 상세근거와 판정은 이 보고서의 grades/keep_or_drop 및 원본감사에 보존한다.','strategy':strategy,'grades':grades,'keep_or_drop':keep}
 Path(BASE+'-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'grades':len(grades),'decisions':len(keep),'evidence':len(evidence)},ensure_ascii=False))
if __name__=='__main__':main()
