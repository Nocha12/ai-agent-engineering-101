import json,pathlib,collections
P='meta/evidence/astra-1111-151301'
def rows(s):return [json.loads(x) for x in open(P+s) if x.strip()]
cs=rows('-commits.jsonl');ms=rows('-commit-metrics.jsonl');markets=rows('-market-metrics.jsonl');cycles=rows('-cycles.jsonl');ts=rows('-tools.jsonl')
grades=[];ev=[];comparisons=[];keep=[]
for i,(c,m,market) in enumerate(zip(cs,ms,markets),1):
 h=c['commit'][:8];title=c['body'].splitlines()[0];a=m['after'];b=m['before']
 metric=f"결제 {b['shop_paid']}→{a['shop_paid']}, 숙성순기여 {b['mature_net']}→{a['mature_net']}원, 구매결제 {b['buyer_paid']}→{a['buyer_paid']}, 시장결제 {market['market_paid_before']}→{market['market_paid_after']}"
 comparisons.append(dict(commit=h,comparison=metric,before=b,after=a,market=market,source=P+'-commit-metrics.jsonl:'+str(i),hypothesis_and_undo=c['body'],note='커밋시각 전후30분. 변경 후 운영배포시각과 다를 수 있음. 동시변경/시장추세로 인과효과 아님; None은 미숙성/결측이며0이 아님. 시장창은12h범위 하한.'))
 evidence_ref=P+'-comparisons.jsonl:'+str(i)
 ev.append(dict(ref=evidence_ref,excerpt=metric,meaning=h+' 전후30분 원장결제·판매원가·연결환불 집계. 원장 원문 참조는 before/after.sample_refs에 보존.'))
 detail='결측: 배포버전별 처리군과 숙성원가/실제가치. 측정: 전후30분 원장과 시장결제. 판정:11/12 03:13 KST 군별숙성20결제에서 현행대비 추가Players>0와 원래undo 미충족; 미달이면 미확정·확대금지.'
 g='unclear'
 if title.startswith(('chore','test:', 'test(', 'Merge','Pin ','Clarify ','W-20261109-2225 test','Keep price-dependent')):
  g='no_effect';detail='기록·시험·주석·병합 자체의 별도 매출효과는 기대하지 않는다. 운영효과는 해당 구현과 함께 판단하며 실패시도도 보존한다.'
 if h in ['9bf55945','1e6b72aa','0ba9e9a6','0a8222fa']:
  g='no_effect';detail='검사 실패로 반려/복원되어 지속 운영효과0. Astra/ Terra의 로컬 통과나 의도만으로 성공을 인정하지 않는다.'
 if h=='e2e9c8a9':
  g='worked';detail='Gc2 조사 종료선을 실행했다:09:41 표본5결제/3명<10결제이고 가정하한 미달. 강제문장1→0, 다른 도구 유지. 제거로 얻은Players 효과는 미식별.'
 if h=='6f1ecb4f':
  detail='커밋가설은 A/B 숙성정규화순익 .0093→.0718(+.0625)이나 C구간[−.136,+.248], B는 A사전값으로 이후제한. 전등급 영구확대의 근거는 부족했고 현재 A만19:30까지다. 군별20숙성/11월12일03:13 추가Players>0 판정, 사람의 종료조건 우선.'
 if h=='fed999c9':
  detail='원문32창 공급12.2→13.8행,상품13.5→17.1개는 공급효과이며 숙성순익/Players는 미확인. 이후 사람결정으로window_fast OFF. 영구정책 성공으로 세지 않으며32창 경제성과 원래undo를11/12 03:13까지 확인, 자동재활성 금지.'
 if h in ['7a52dcef','de9c713f','7818110b','4f6561a4','36661339']:
  detail='기본OFF 유한창/상품회전·폭·식품품절 실험. 코드도입과 처리군노출은 다르다. 결측:SKU/창별배정·실공급·6h숙성순익. 이번두창총상한절단0/0·SKU35/33, G03입고1판매0.11/12 03:13 군별20숙성·추가Players>0/원래undo로판정; 자동기본ON 금지.'
 if h in ['c0c43bb3','b8f3706d','a6452e32','637d7cb0','87c99c4d']:
  detail='사람의 공급행10→18·bound20·호출간격/식품원가3M→8M 변경이다. 현금완충은 유지. 결측:실제창별추가공급/미판매비용과숙성순익. 이번두창총상한절단0/0을측정;11/12 03:13 군별20숙성/두완료창 추가Players>0와 원래undo 판단. 규모를현금이유로임의축소하지않음.'
 if h=='af19aecb':
  detail='구매 Saved 최소 .33으로상향. 결측:낮아진지급률과실제가치/분모의순효과.이번구매결제전후와팩Saved.3266을측정;11/12 03:13 군별20숙성주문에서추가Saved와Players>0/원래undo판정. 단순명목할인을성공으로세지않음.'
 grades.append(dict(change=h+' '+title[:90],reason=metric+'; '+detail+' 근거 '+evidence_ref,grade=g))
 # Each code/settings/prompt commit has a decision, including superseded attempts.
 keep.append(dict(item=h,decision='keep',reason='현재 반영 상태와 실패이력을 유지. '+detail+' 파일/설정 원문: '+P+'-commits.jsonl:'+str(i)+' 및 -settings.jsonl:'+str(i)))
with open(P+'-comparisons.jsonl','w') as f:
 for r in comparisons:f.write(json.dumps(r,ensure_ascii=False)+'\n')
for i,r in enumerate(cycles,1):
 a=r.get('answer') or {};name=r['cycle'];check=a.get('strategy_check') or {};g='unclear'
 if name.startswith('astra'):
  g='no_effect';reason='직전Astra는전체검사반려로변경복원. 후보수리/문서/도구삭제의운영효과0. 로컬2013통과와하네스실패를구분한다.'
 else:
  reason='새패치0·산식일치·호출상한복원없음. 총상한구속0/0이므로미확대는적절하고SKU세조건이충족됐다는증거도없다. '
  if name=='terra-1111-100443':g='worked';reason+='Gc2 조사5결제<10의원래철회조건으로문장제거완료. 전략유지와분리하여평가한다.'
  elif name=='terra-1111-050347':g='no_effect';reason+='포럼대체검색지침은반려돼문장변화0. 새전략전환없음.'
  elif name in ['terra-1111-111706','terra-1111-123136','terra-1111-134407','terra-1111-145658']:
   g='harmful';reason+='계산가능한공급창/처리군결측과미완료회귀를반복했다. 특히14:58은새결측을말하면서이미지난14:44서지판정선을재사용해다음행동을못정했다. 운영손실을뜻하는등급이아니라평가절차감점이다.'
  else:
   reason+='전략유지는12h경쟁자추세와양립하지만추가효과는미확인. 이번원장/두창측정으로보완했다.11/12 03:13 군별숙성20결제·추가Players>0,미달확대금지.'
  reason+=' 전략검사 '+str(check.get('keep_or_shift'))+'; '+a.get('grade_summary','')[:180]
 grades.append(dict(change=name+' 전략·규칙·검증',reason=reason+' 근거 '+P+'-cycles.jsonl:'+str(i),grade=g))
 ev.append(dict(ref=P+'-cycles.jsonl:'+str(i),excerpt=name,meaning='원문전략/수치/실험판정/하네스반려기록'))
for i,t in enumerate(ts,1):
 name=t['tool'];decision='drop' if name=='auto_auto_market_brief' else 'keep'
 reason=f"이번 {t['calls']}호출/{t['failures']}실패. "
 reason+=('auto_market_scan과코드byte동일·0호출이므로도구와전용검사실제삭제,원본-dropped-tool.json보존.' if decision=='drop' else '독립기능/기존의미검사를유지. 실행성공을Players성과로보지않음.')
 keep.append(dict(item='tools/'+name,decision=decision,reason=reason))
 ev.append(dict(ref=P+'-tools.jsonl:'+str(i),excerpt=f'"calls": {t["calls"]}, "failures": {t["failures"]}',meaning=name+' 구간사용/실패'))
for id in ['g2-loss-target-1110','concentration-probe-1110']:
 keep.append(dict(item='meta/experiments.json:'+id,decision='drop',reason='이미drop종료된항목을이번활성큐에서실제로제거. 원본-queue-before.json/종료로그는보존하고재실행안함.'))
for path in ['tools/review_accounts','strategy_screen.py','meta/strategy.md','meta/terra.md','meta/REPORT-notes.md']:
 keep.append(dict(item=path,decision='keep',reason='이번결제연결/유효가격집단진단과판정선. 직접추가Players는미식별이며테스트와직렬실험으로검증.'))
for suffix,line in [('-reviews.jsonl',1),('-screen.jsonl',2),('-period.jsonl',1),('-guards-v2.jsonl',1),('-guards-v2.jsonl',2),('-rules.jsonl',2),('-rivals-v3.jsonl',3),('-rivals-v3.jsonl',4)]:
 text=open(P+suffix).read().splitlines()[line-1]
 ev.append(dict(ref=P+suffix+':'+str(line),excerpt=text,meaning='이번고정팩시각의원장·공급·규칙측정. 경쟁자수치는URL연결범위하한.'))
for suffix,needle in [('-screen-before.txt','FAILED (failures=1)'),('-focused-tests.txt','Ran 6 tests'),('-baseline-tests.txt','Ran 2173 tests')]:
 for i,line in enumerate(open(P+suffix),1):
  if needle in line:ev.append(dict(ref=P+suffix+':'+str(i),excerpt=line.strip(),meaning='변경전실패/관련검사/HEAD전체검사원본'));break
out=dict(cycle='astra-1111-151301',previous='astra-1111-025700 rejected; commits since 2026-11-11 02:57 KST',grades=grades,keep_or_drop=keep,evidence=ev)
pathlib.Path(P+'-review.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(dict(grades=len(grades),keep_or_drop=len(keep),evidence=len(ev)))
