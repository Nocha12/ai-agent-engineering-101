import json,subprocess
from pathlib import Path
BASE='meta/evidence/astra-1110-143630'
rows=[json.loads(l) for l in open(BASE+'-audit.jsonl')]
grades=[]; keep=[]; evidence=[]; metrics=[]
for r in rows:
 if r['kind']!='commit':continue
 sha=r['commit'];a=r['before'];b=r['after'];title=r['title']
 m={'commit':sha,'paid':[a['paid'],b['paid']],'mature_paid':[a['mature'],b['mature']], 'net':[a['mature_net'] if a['mature'] else None,b['mature_net'] if b['mature'] else None], 'buyer_paid':[a['buyer_paid'],b['buyer_paid']], 'market_paid':[a['market_paid'],b['market_paid']]}
 metrics.append(m);ref=f'{BASE}-commit-metrics.jsonl:{len(metrics)}'
 missing='처리군별 추가 Players와 6h 숙성순익이 결측'
 grade='unclear'
 if title.startswith(('chore','test')) or sha in ['b35315a4','0f8b2953']:
  grade='no_effect';missing='기록·검사 또는 반려/원복은 독립 운영효과가 없다'
 if 'default off' in title.lower() or 'default OFF' in title or '기본 OFF' in title:
  missing='기본 OFF 구현이며 처리 노출/대조군의 추가 성과가 결측'
 if sha=='425e7dcb':
  grade='harmful';missing='원장행 크기 변경은 tail 읽기 범위를 줄이는 구조적 회귀; 0041a964가 별도 파일로 대체했고 현재는 구버전 미유지'
 if sha=='30a5d637':
  missing='G2 변경 뒤 숙성9건 +210294원/23366원·실제 목표구속 미확인; 최소10건과 구속1건 부족'
 judge='측정: 커밋 전후30분 정산/시장량을 이번에 계산했다. 판정: 활성화된 처리·대조 각30건과6h 숙성 후 순기여/Players 추가효과>0이면 유지; 11/11 02:36 표본미달이면 성과 미확정, 확대 중단하고 해당 시험 처리만 OFF. 사람의 창/undo 우선.'
 if sha=='30a5d637':judge='측정: 04:30 이후 코호트와 두 공급창 unit ID. 11/10 16:30 숙성10건·건당≤0·실제매입감소≥1 모두 충족만 유지, 그 외2→3복원.'
 reason=f"{ref} 정산 {a['paid']}→{b['paid']}, 숙성순기여 {m['net'][0]}→{m['net'][1]}원, 시장정산 {a['market_paid']}→{b['market_paid']}. {missing}. 동시 수정·수요/구매자 구성·짧은 창이 교란한다. {judge if grade=='unclear' else ''}"
 grades.append({'change':sha+' '+title,'grade':grade,'reason':reason})
 keep.append({'item':sha+': '+', '.join(r['files']), 'decision':'keep','reason':'최종 HEAD의 관련 구현·검사/실패 이력 유지. '+ ('구버전은 이미 후속 커밋에서 대체되어 복원하지 않는다.' if sha=='425e7dcb' else missing+'; 운영 성공으로 간주하지 않고 원래 undo와 위 판정선을 적용한다.')})
 # Preserve exact code/settings diffs to audit all changes, not just subjects.
 diff=subprocess.check_output(['git','show','--format=','--',*[]],text=True) if False else subprocess.check_output(['git','show',sha,'--format=','--','pricing.json','meta/terra.md','meta/strategy.md'],text=True)
 if diff:Path(BASE+'-settings-'+sha+'.diff').write_text(diff)
Path(BASE+'-commit-metrics.jsonl').write_text(''.join(json.dumps(m,ensure_ascii=False)+'\n' for m in metrics))
for i,m in enumerate(metrics,1):evidence.append({'ref':f'{BASE}-commit-metrics.jsonl:{i}','excerpt':json.dumps(m,ensure_ascii=False),'meaning':'원장 결제·주문연결 환불·원가를 고정마감으로 계산한 전후30분; 미숙성 null, 동시변경 교란.'})
for i,line in enumerate(open('meta/grades.jsonl'),1):
 d=json.loads(line)
 if i<132:continue
 cyc=d['cycle'];s=d.get('strategy_check') or {};reason=s.get('reason','')
 grade='unclear'
 if d['status']=='rejected':grade='harmful';note='실패를 확인하기 전에 완료 처리해 동일 병렬견적 실패로 반려. 운영효과0; 재검사 완료까지 성공 금지.'
 elif cyc in ['terra-1110-123833','terra-1110-135024']:
  grade='harmful';note='경쟁 SKU 비교는 원장에서 지금 계산 가능하지만 반복해서 미완결로 남겼다. 전략 유지 자체보다 반복 결측 방치에 감점. 이번 rival 집계를 다음 주기 인용한다.'
 else:note='새 패치없음/산식 일치·호출무상한·총량 보존은 준수. G2 최소표본과 목표구속 부족을 성공으로 포장하지 않은 점은 적절. 대체 정책 우위는 미확정.'
 reason=f'meta/grades.jsonl:{i} {note} '+reason+' 측정: 이번 고정마감 원장과 G2 숙성9건 +210294원, 총상한 절단0/0 재계산. 16:30 G2 숙성10건·순기여≤0·실제구속≥1 외에는2→3복원. 전략전환은 두 연속 주기에서 동일 대안의 추가효과>0일 때만.'
 grades.append({'change':cyc+' 규칙·시간별 성과·전략점검','grade':grade,'reason':reason})
 evidence.append({'ref':f'meta/grades.jsonl:{i}','excerpt':json.dumps(d,ensure_ascii=False)[:160],'meaning':'해당 Terra 점검의 원문; 기록된 상대속도와 아직 측정하지 않은 항목을 별도 평가.'})
for i,r in enumerate(rows,1):
 if r['kind']=='tool':
  keep.append({'item':'tools/'+r['name'],'decision':'keep','reason':f"이번 구간 호출{r['calls']}·실패{r['failed']}. "+('비정산/참조미연결을 이번에 수리하고 실제 sandbox 의미검사3개 통과. 유효성 검사는 평점 개선 증거와 구분한다.' if r['name']=='auto_forum_ledger_digest' else '기존 기능 유지; 무호출은 무효/무가치 증거가 아니며 실행성공만으로 의미 정확성을 선언하지 않는다.')})
  evidence.append({'ref':f'{BASE}-audit.jsonl:{i}','excerpt':json.dumps(r,ensure_ascii=False),'meaning':'도구별 이번 주기 호출·실패 전수 집계.'})
keep.append({'item':'concentration-probe-1110 대기항목','decision':'drop','reason':'이전2h 단일구매자 후보는 이번 수요자료로 뒷받침되지 않아 큐에서 제거. 아직 GOAL 문장은 적용되지 않아 코드 undo 없음. 새 포럼 참조 검증 실험으로 대체.'})
review={'cycle':'astra-1110-143630','cutoff':'2026-11-10T14:36:43+09:00','previous_review':'astra-1110-021612','grades':grades,'keep_or_drop':keep,'evidence':evidence,'limits':'커밋 시간(커미터 시간)을 기준으로 포함; 작성시각이 이전인 cherry-pick도 포함. 장부에 늦게 들어온 마감 이전 거래가 팩과 다를 수 있다. 원장 집계는 정산/환불/원가 기준이며 보관비 제외. 첫 guard 집계는 food stock cost를 전체 상한으로 오인했으므로 guards-v2만 사용.'}
Path(BASE+'-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
print(len(metrics),'commits',len(grades),'grades',len(keep),'keep/drop')
