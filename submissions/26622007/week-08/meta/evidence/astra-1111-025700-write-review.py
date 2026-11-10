import json
from pathlib import Path
C='astra-1111-025700'; base='meta/evidence/'+C
p=json.load(open(base+'.json'))
rows=[(i,json.loads(l),l.strip()) for i,l in enumerate(open(base+'-audit.jsonl'),1)]
evidence=[];grades=[];keep=[]
def cite(path,i,excerpt,meaning): evidence.append(dict(ref=f'{path}:{i}',excerpt=excerpt,meaning=meaning))
for i,r,line in rows:
 if r['kind']=='commit':
  b,a=r['before'],r['after'];name=r['commit'];title=r['title']
  reason=f"전후 {b['hours']}/{a['hours']}h 결제 {b['paid']}→{a['paid']}, 구매지급 {b['buyer_paid']}→{a['buyer_paid']}, 시장결제 {b['market_paid']}→{a['market_paid']}, 숙성 {b['mature']}→{a['mature']}, 확인원가 순기여 {b['mature_net']}→{a['mature_net']}원, 원가누락 {b['missing_cost']}→{a['missing_cost']}. {base}-audit.jsonl:{i}. "
  g='unclear'
  if title.startswith(('chore','test','wip','revert')):
   g='no_effect';reason+='기록/검사 또는 반려·원복 자체의 독립 운영효과는 인정하지 않는다. 실패 기록은 유지한다.'
  else:
   reason+='결측: 동시 실험·수요 변화와 분리한 해당 변경의 처리군 및 대조군. 측정: 이번 고정 마감 원장의 전후 결제·주문연결 환불·숙성 원가 집계. 판정: 11/11 14:57 KST까지 같은 SKU·등급 각 군 숙성20건에서 추가 순기여/시간>0 및 Saved/주문 비악화; 미달·표본부족은 성공 승격·확대 금지, 사람의 원래 종료/undo 조건 우선. 숙성0의 순기여0은 손익0이 아니다.'
  grades.append(dict(change=name+' '+title[:110],grade=g,reason=reason))
  keep.append(dict(item=name+' '+', '.join(r['files']),decision='keep',reason='현재 적용 또는 원복 상태 유지. '+('실패한 구현을 다시 활성화하지 않는다.' if g=='no_effect' else '인과효과는 미확정이며 위 판정선 및 원래 실험의 철회 조건을 적용한다.')))
  cite(base+'-audit.jsonl',i,'"before": '+json.dumps(b,ensure_ascii=False),name+' 전 수치; 원본 원장 ref 포함')
  cite(base+'-audit.jsonl',i,'"after": '+json.dumps(a,ensure_ascii=False),name+' 후 수치; 원본 원장 ref 포함')
 if r['kind']=='cycle':
  g='harmful' if r['status']=='rejected' or r['cycle'].startswith('terra') else 'unclear'
  reason='패치 3.5 점수와 호출 무상한은 유지했다. 새 패치는 없어 발견 지연 없음. 총상한 절단0/0이므로 미인상을 감점하지 않는다. 전략의 경쟁자 성장 비교는 유효하나 독립 추가 Players는 미확정이다. '
  if r['status']=='rejected':reason+='검증 또는 인용 실패로 전달되지 않은 작업은 감점한다. '
  if r['cycle'].startswith('terra'):reason+='전체 검사를 중단하거나 진행 중인 채 보고하는 일이 반복되어 검증 절차를 감점한다. 좁은 관련 검사의 성공과 전체 성공을 분리해야 한다. '
  reason+=f'근거 {r["ref"]}; 측정은 이번 전후 원장 감사. 결측은 실제 3판단→최종공급 연결 및 독립효과. 11/11 14:57까지 3판단 모두 SKU 근거와 최종공급 연결, 검사 최종 종료코드 확인; 미달이면 성공 판정 취소, 신규 확대 보류.'
  grades.append(dict(change=r['cycle']+' 전략·규칙·확대·검증 점검',grade=g,reason=reason))
  cite(base+'-audit.jsonl',i,'"cycle": '+json.dumps(r['cycle']),reason)
 if r['kind']=='tool':
  keep.append(dict(item='tools/'+r['name'],decision='keep',reason=f"이번 구간 {r['calls']}호출/{r['failed']}실패. 기존 역할과 수정된 의미검사를 유지하며 실행성공만으로 거래효과를 인정하지 않는다."))
  cite(base+'-audit.jsonl',i,line,'도구별 구간 사용·실패')
keep.append(dict(item='tools/auto_auto_market_context',decision='drop',reason='신규 미사용 중복도구를 이번에 삭제했다. 전체 inventory 반환과 원시 행 검색은 기존 의미검증 연구도구보다 추가 가치가 없다. 전용 테스트는 없고 원본은 dropped-tool.json에 보존했다.'))
for i,line in enumerate(open(base+'-screen.jsonl'),1):cite(base+'-screen.jsonl',i,line.strip(),'공개 규칙 및 정가 이하 하한 통과 실측; 경쟁자 원가는 가정')
for i,line in enumerate(open(base+'-supply.jsonl'),1):
 if i<=2:cite(base+'-supply.jsonl',i,line.strip(),'최근 두 공급창 가드 기록; 반복 평가행이며 독립 공급 실패 수 아님')
options=[dict(direction='현행 규모 전략과 진행 중 집중도 조사를 완료',expected_profit='현행 대비 추가 ΔPlayers 기준 0. 현재 1.646/7위, 12h 선두격차는 3h속도 기준 .08037, 12h속도 기준 .08817; 바로 위 .06476/.05305. 조사 자체의 수익은 0으로 둔다.',risk='상위 경쟁자가 더 빨리 성장한다. 후보의 정가초과 가격과 보증위험을 일반 마진으로 오인할 수 있다.',test='09:55까지 기존 조사3건·결제10건·구매자3명과 정가 이하 하한 통과 확인. 미달이면 해당 GOAL 문장 제거.'),dict(direction='검증된 60만+ SKU 및 회전 확대',expected_profit='추가 book 35.824M/매출45.635M을 움직이는 문턱보다 더 확보하면 조건부 +.00769/+.00462. 선두 관측 420.37M 중 고가343.29M, inextro 601.64M 중557.62M. 현재 대비 추가 판매량·원가 미식별이므로 기대값 미확정.',risk='서지·환불·고원가 단위의 잔류. 총상한1B 절단0/0이라 인상 직접효과0. 상대원가 비공개.',test='두 공급창의 실제 목표구속, 60만+ 과반 판매, 숙성순익 양수와6h book 비하락 모두 충족하는 한 SKU만 한 단계.'),dict(direction='Saved 구매 및 근거 기반 포럼 집중',expected_profit='Saved .3237→움직이는 .3239 문턱을 추가로 넘으면 조건부 +.00794, forum .897→.900이면 +.0009. 사람의 식품89% 파일럿과 겹쳐 추가 효과 미식별; 동일 경쟁자 3h/12h 시나리오 적용.',risk='지급률 감소·식품 실제가치 결측·중복 실험에 의한 귀속 오류. 포럼 평점은 게시량과 다르다.',test='기존 사람 실험을 군별 지급·사용기한 실제가치·Saved 분모까지 연결해 판단; 새 가격 스위치 추가 없음.')]
strategy=dict(options=options,chosen='현행 규모 전략 유지. 진행 중 집중도 실험을 먼저 종료하고 정가 이하 검증 자료를 사용하는 작은 조사만 뒤에 직렬 배치한다. 현행보다 더 큰 추가 Players를 입증한 옵션이 없다.',experiment='concentration-probe-1110은 11/11 09:55 기존 기준으로 종료. 다음 affordable-demand-1111은 새 하한 통과 자료를 3회 확인해 결제10건·독립구매자3명 후보≥1을 시작+12h에 판정; 자동 매입 없이 한 단위 후속시험 제안까지만 허용.')
gap=dict(missing='각 정책의 동시 변경과 분리한 추가 Players 및 후보의 정가 이하 보증하한 통과 결제10건·구매자3명',measurement='고정 마감 전후 원장·환불·원가 감사와 새 정가 이하 집계 실행. G796e9122 전체11건/7명 중 유효1건/1명; 기존 하한초과3건과 구분.',judge_line=dict(metric='정가 이하 보증하한 통과 결제10건·독립구매자3명을 갖춘 후보 수',direction='above',threshold='0',min_sample='조사3회, 후보별 중복제거 결제10건·독립구매자3명',expires='현재 실험 2026-11-11 09:55 KST; 후속은 시작+12h',undo='현재 실험 미달이면 Gc2f7811b 조사 문장 제거; 후속 미달이면 후속 GOAL 문장만 제거. 의미검증 자료 수리는 유지하며 자동매입 금지'))
review=dict(changed=True,hypothesis='정가 이하 하한 통과와 신원 결측을 구분하면 고가·집중 거래를 확장 가능한 수요로 오인하는 후보 판정이 줄어든다.',change_summary='공개 수요 화면의 유효 구매자·정가 이하 하한 통과 집계와 유한 가격 검사를 추가했다. 미사용 중복 자동도구를 제거하고 전략·직렬 큐·Terra 검증 지침·보고서를 갱신했다.',target_metric='Players 순위; 후보의 유효 결제·구매자 수와 검증 완료율',expected='직접 ΔPlayers는 미확정이며 이번 자료 수리의 보수적 기준은0. G796e9122 하한초과3건에서 정가 이하 유효1건을 분리한다.',summary='Players 1.646, 7위. 현행보다 우수한 추가효과가 입증되지 않아 규모 전략을 유지하고 잘못된 후보 근거와 반복된 미완료 검증을 바로잡는다.',evidence=evidence,evidence_gap=gap,strategy=strategy,grades=grades,keep_or_drop=keep)
Path(base+'-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
print(len(grades),len(keep),len(evidence))
