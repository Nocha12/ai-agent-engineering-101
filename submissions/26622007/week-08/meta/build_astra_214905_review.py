"""Build the review from fixed-cutoff evidence, never edits operational history."""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hard_limits
BASE='meta/evidence/astra-1105-214905'
audit=[json.loads(l) for l in Path(BASE+'-audit.jsonl').read_text().splitlines()]
evidence=[];grades=[];keeps=[]
def ev(path,n,meaning,excerpt=None):
 line=Path(path).read_text().splitlines()[n-1]
 evidence.append(dict(ref=f'{path}:{n}',excerpt=excerpt or line,meaning=meaning));return f'{path}:{n}'
def keep(item,reason,decision='keep'):keeps.append(dict(item=item,reason=reason,decision=decision))
notes={
'ab141e7':('unclear','G6/G75 축소와 침구시험·검색수정이 섞였다. 침구 재시험은 실제입고원가 연결 후 +14,932원으로 성공했지만 묶음의 독립효과는 없다.'),
'aef247c':('worked','현금식에서 공개 book식으로 수정됐다. 현재 공개가중치 불일치0이며 실제 보드1.279→1.380으로 비교해야 한다.'),
'8401d18':('worked','시험이 공급창 전체를 막는 경로와 선발송을 회귀검증했다. 이익증가의 단독원인으로 보지 않는다.'),
'a83df1f':('worked','잘린 시험줄의 큐 복구는 회귀시험으로 유지한다. 유실방지의 단독 매출효과는 분리되지 않았다.'),
'0903559':('worked','/rules 자동조회와 scoring 대조가 현재 Patch3.3을 감지한다. 패치를 사람이 먼저 고쳤다는 점은 별도 감점이다.'),
'497a10c':('worked','공개 가중치 이름과 허용오차를 검사한다. 현재 공개식 비교 불일치0.'),
'fee4a9b':('worked','profit 별칭을 공개 book 일치로 잘못 통과시키지 않는 검증을 유지한다. 공식회귀시험 통과.'),
'1836376':('no_effect','문서 정리 자체의 매출효과를 주장하지 않는다. 현재 목표 문구가 공개식과 일치한다.'),
'e356a5a':('unclear','사람의 명시적 구매자 거절 정책이다. 별점과 손실 회피의 반사실은 결측이며 메타의 임의복원 대상이 아니다.'),
'58caaa9':('worked','거절 경로의 읽기·잠금 보강 회귀시험 통과. 사람의 정책은 그대로 보존한다.'),
'05b3802':('worked','Everyone 순위·성장·경보가 표시된다. 다만 같은 날짜 이후 공식수정은 놓쳐 이번에 objective signature 비교로 고쳤다.'),
'504496c':('worked','깨진 JSONL 복구·별칭·경보 검증 유지. 포럼 하락을 답변 없이 넘긴 Terra는 별도 감점한다.'),
'2872bdc':('unclear','서지비용을 반영하지만 매입 억제로 놓친 유료주문의 반사실은 없다. 전체수익증가는 규모확대와 혼재한다.'),
'a8f0786':('worked','서지 원가·SKU점유·검증품목 단위 제한 회귀검증 통과. 손익효과는 다른 서지수정과 분리하지 않는다.'),
'c6292b5':('unclear','바이어 지급56/94→63/87, 명목절약7,935,435→14,749,078(history.jsonl:11). 적격견적 우선순위와 후속limit검사 등이 혼재해 독립잉여효과는 미확정이다.'),
'251d026':('worked','검증SKU 합계1단위 및 신규가게 탐색 한자리 회귀검증 통과. 단기순기여를 코드효과로 단정하지 않는다.'),
'2de5c89':('unclear','초기4M 고가진입은 이후15M 설정과 겹친다. 이전단계 단독효과는 식별되지 않았다.'),
'71bb9cb':('worked','4M에서 소파가 매트리스 보충을 밀어내는 재생근거에 대응했다. 이후15M 정책이 그 보류를 대체했다.'),
'4972ef3':('worked','한번에 하나의 실험과 판정선 검증이 동작한다. 만기를 최소대기로 오독하는 지침은 이번에 수정했다.'),
'ce316e6':('worked','코드없는 시작·실험로그 직접편집을 거절하는 회귀검증 통과. 기존로그는 손대지 않았다.'),
'ea105d3':('worked','Luna 호출상한 해제가 운영정책에 반영됐다. 이번 팩의 호출수는 표시값일 뿐이며 제한을 복구하지 않는다.'),
'eafa509':('worked','무상한 시각목록 누적 방지와 비용표시 회귀검증 통과. 호출 절감 목표로 사용하지 않는다.'),
'8d332f3':('no_effect','가격동향 글의 관측10분 중앙값 .487(n38)은 구매기록 .487(n56)과 같다. 동연령1h는 양쪽 모두 결측이라 우위 없음.'),
'f57edce':('worked','가격동향의 중복seq·거짓등급·0분모 방어 회귀검증 통과. 득표 우위는 관측되지 않았다.'),
'ff56b08':('unclear','모델 전송·fallback 구조는 회귀시험 통과. 협상성공·지연 개선의 독립효과는 거래혼합과 동시변경으로 결측이다.'),
'46c4e27':('unclear','1점3회 거절은 사람 결정이다. 회피한 손실과 거절한 흑자거래의 반사실이 없어 임의경제성 결론을 내리지 않는다.'),
'0d5e2ce':('no_effect','이미 구현된 포럼 실험 중복을 큐에서 제거했다. 직접적인 손익변경은 없다.'),
'530146b':('worked','검증된 영수증 URL만 집계하고 손상행을 넘기는 방어 회귀검증 통과. 거절정책은 수정하지 않는다.'),
'4cc7be4':('worked','URL 타입 손상 방어 회귀검증 통과. 금융효과로 해석하지 않는다.'),
'8353345':('no_effect','사람의 한도 변경은 허용공간이다. 운영15M 설정과 분리된 즉시 판매효과를 주장하지 않는다.'),
'bc658fa':('worked','규모확대 후 G8 숙성15건+5,785,807·G052 10건+1,354,185가 생겼다. book +26,279,819는 다른수정도 섞인 방향성 증거다.'),
'441a08e':('worked','사람이 고친 경계·상승폭·부호검사 회귀시험 통과. 보호파일은 그대로 둔다.'),
'9eb503e':('worked','허용되지 않은 모델 도구동작 차단 회귀시험 통과. 11건 실시험 도구동작0이라는 커밋 근거와 일치한다.'),
'33ddbc3':('worked','사람의 거절설정·게이트 보호 회귀검증 통과. 이번 검사도 seller 동일·보호위반0이다.'),
'6ce25f6':('worked','카펫1단위 지급276,396-원가225,163-환불0-추정보관53.30=51,179.70. 두창내 판매·6h 숙성 성공.'),
'8016a1e':('no_effect','q00 예외는 사람의 정책이다. 독립손익효과를 주장하지 않고 동일값 보존한다.'),
'89db408':('no_effect','150M 경계 확대는 실제 재고매입이 아니다. 운영15M 유지, 현재 확대 세조건 미달.'),
'8c35842':('worked','규모3조건은 올바른 운영 판정선이다. 반복 미측정을 막기 위해 이번에 자동단위조회가 추가됐다.'),
'3c9fd42':('worked','타입우회와 고가기준가 우회 차단 회귀시험 통과. 보호파일 변경 없음.'),
'1de0cc3':('no_effect','병합 자체의 경제효과는 없고 카펫 변경은 6ce25f6에서 별도 평가했다.'),
'9f22139':('worked','재시작으로 빠진 시작기록을 사람이 보완했다. 카펫이 현재 running으로 조회돼 유실은 복구됐다.'),
'1dbc1ed':('no_effect','목표·순위문서 수정의 직접수익은 없다. 이번 전략에 현재 동일공식 비교를 통합했다.'),
'0782954':('unclear','고가 공급순서 변경은 15M·SKU목표 수정과 겹친다. 고가 판매는 발생했지만 순서만의 추가판매는 결측이다.'),
'7fb1f34':('no_effect','커밋의 교차10회 실측은 캐시적중 변화없음이다. 관측필드만 유지하며 호출수 감축으로 바꾸지 않는다.'),
'd374122':('unclear','G8 확대후 숙성흑자는 확인되나 Gb5 축소는 당시 잔류기준이었다. 현재3건 -8,398은 확대금지 근거이며 축소10건 문턱 미달이다.'),
'42c174b':('worked','견적 당시 limit 재검증과 next_steps 회귀검증 통과. food-reserve는 관찰만 하며 실제예약으로 오해하지 않는다.'),
'a07ca72':('worked','auto_auto_market_brief의 SKU키·정산중복·판매자필터 의미회귀시험 통과. 현재54호출 실패0은 실행성만 증명한다.'),
'81fd8cc':('worked','공급429는16시16/49줄→18시0/43→20시1/40. 창마감 재검사의 기대방향과 맞지만 공급구성·시장변화도 설명 가능하다.'),
'fc36f9c':('harmful','G03을 손실 없이 잔류만으로2→1 축소했다. 현재숙성6건+1,768,035로 손실근거 없어서 이번에1→2 복원한다. 잃은 매출액은 결측이다.'),
'a69b853':('worked','바이어 전후1시간 지급33/74→52/75, 짧은보증20→2, 명목절약3,959,288→7,827,085. 기대방향이나 주문혼합·다른수정이 혼재하고 숙성잉여는 아니다. history.jsonl:13.')}
for i,x in enumerate(audit,1):
 if x['kind']!='commit':continue
 b,a=x['before'],x['after'];sha=x['sha']
 grade,reason=notes.get(sha,('no_effect','운영변경 없는 하네스 기록이다. 해당 Terra 평가·전략점검은 별도 행에서 판정한다.'))
 excerpt='"before": '+json.dumps(b,ensure_ascii=False)+', "after": '+json.dumps(a,ensure_ascii=False)
 ref=ev(BASE+'-audit.jsonl',i,sha+' 전후30분 유료정산·환불후 알려진 순기여; 시장거래·원가결측·미숙성 병기',excerpt)
 numeric=f"판매 {b['paid']}→{a['paid']}, 알려진 순기여 {b['net_after_refunds']}→{a['net_after_refunds']} ({ref}). "
 gap=''
 if grade=='unclear':
  gap=' 독립효과 결측: 이번 전후30분 원장 조회. 11/06 09:49까지 동일상품·등급 숙성20건의 시간당 순기여가 직전동길이 이상이면 잠정유지, 미달은 혼재원인 재검토; 사람 정책·총상한은 임의축소하지 않는다.'
 if sha=='c6292b5': gap=' 독립효과 결측: 이번 동일1시간 주문·지급을 조회했다. 11/06 09:49까지 후속100주문 지급률>=72.4% 및 숙성잉여/주문>=직전100주문이면잠정유지; 미달이면견적순서만재검토.'
 grades.append(dict(change=sha,grade=grade,reason=numeric+reason+gap))
 # Each commit's entire settings/code/prompt surface, not only one selected field.
 files=[f.split('week-08/',1)[-1] for f in x['files']]
 moves=[]
 try:
  old=json.loads(subprocess.check_output(['git','show',sha+'^:./pricing.json'],text=True,stderr=subprocess.DEVNULL))
  new=json.loads(subprocess.check_output(['git','show',sha+':./pricing.json'],text=True,stderr=subprocess.DEVNULL))
  moves=hard_limits.policy_diff(old,new)
 except (subprocess.CalledProcessError,ValueError):pass
 decision='drop' if sha=='fc36f9c' else 'keep'
 desc='; '.join(moves) or ', '.join(f for f in files if not f.startswith('meta/evidence/') and not f.startswith('meta/last-'))
 keep(sha+' '+desc,reason+(' 원복 완료: G03=2, 테스트 기대값도2.' if decision=='drop' else ' 다른 변경과 혼재한 경제효과는 잠정이며 보호설정은 그대로다.'),decision)
for i,x in enumerate(audit,1):
 if x['kind']!='terra':continue
 c=x['cycle'];b,a=x['before'],x['after'];ref=ev(BASE+'-audit.jsonl',i,c+' 전략판단 및 전후1시간 측정', '"before": '+json.dumps(b,ensure_ascii=False)+', "after": '+json.dumps(a,ensure_ascii=False))
 tag=c.rsplit('-',1)[-1];grade='unclear';reason='규모전략 유지 자체는 타당하지만 공급창 단위측정을 완료하지 않았다.'
 if tag=='100025':grade='harmful';reason='공개 book 전환 이후 현금식을 유지한 직전 Astra 오류를 고치지 못했다. 패치는 이후 사람이 반영했다.'
 elif tag=='111220':grade='worked';reason='사람이 반영한 Patch3.3과 같은 book 기준으로 검토했다. 스스로 먼저 패치를 발견한 성과는 아니다.'
 elif tag in ['134555','205737']:grade='harmful';reason='no_change_unsupported: 열린 경보 응답 또는 근거조건을 충족하지 못했다. 단위회전 조회를 반복 미완료로 남겼다.'
 elif tag=='160953':grade='worked';reason='생성도구 의미오류를 실제정산으로 고쳤다. 패치식·호출무상한 유지. 경제인과효과는 미확정이다.'
 elif tag=='183335':grade='harmful';reason='잔류만으로 G03을 축소해 현재 사람의 숙성손실 규칙과 충돌한다. 이번 목표2로 복원. 당시 총상한을 낮추지는 않았다.'
 elif tag=='194604':grade='harmful';reason='카펫숙성 양수51,180을 직접 계산하고도 12시간을 최소대기로 읽어 계속했다. 조기완료 가능하도록 지침수정.'
 else:reason+=' 당시 고가숙성0은 기다릴 근거지만 이미 있는 입출고조회는 기다릴 이유가 아니다.'
 if grade=='unclear':reason+=' 결측은 당시 창별지급률; 이번 scale_measure에서 새매입표본0·잔류를 확인. 다음2창 차단각1회·과반지급·숙성흑자·book비하락이면1단계확대, 아니면15M유지. 판정11/06 09:49.'
 grades.append(dict(change=c+' 전략·평가',grade=grade,reason=f"판매 {b['paid']}→{a['paid']}, 알려진 순기여 {b['net_after_refunds']}→{a['net_after_refunds']} ({ref}). "+reason))
for i,x in enumerate(audit,1):
 if x['kind']!='tool':continue
 ref=ev(BASE+'-audit.jsonl',i,'도구 사용·실패·자가시험 전수검토')
 keep('tools/'+x['name'],f"이번 {x['calls']}호출·실패{x['failed']} ({ref}). "+('수정된 정산·SKU 의미회귀시험 통과. 중복기능은 있지만 유료효과를 과장하지 않고 잠정유지.' if x['name'].startswith('auto_') else '실패로 제외된 도구 없음. 미사용은 유익성 증명이 아니며 다음12시간 근거인용≥1을 확인한다.'))
keep('tools/auto_market_lookup 및 auto_catalog_ledger_search','이전 사이클에 이미 제거된 도구다. 과거 호출누계는 현재도구로 오해하지 않고 재도입하지 않는다.')
keep('g6-a-ask-cap-343k 대기안','가격실패 주문수가 없고 동일10분 포럼 득표도 개선없다. 규모 우선순위에 비해 근거가 약해 큐에서 실제 제거했다. 350000 운영호가는 그대로다.','drop')
keep('tools/forum_demand + test_forum_demand.py','수요글→분류·정산·보유재고 읽기 전용 연결. 실제명령/지급/메시지 기능 없음; 샌드박스 및 의미검증 통과.')
keep('scale_measure.py + scorecard.py + test_scale_measure.py','반복된 두창회전 결측을 자동조회로 바꾼다. 출고를 판매로 오인하지 않으며 표본0은 null. 자동확대는 하지 않는다.')
keep('meta_harness.objective_changed + test_objective_comparison.py','09:38 현금식과21:49 book식 혼합을 false→true로 표시. 날짜만으로 공식전환을 판정하던 오류를 수정한다.')
keep('meta/strategy.md·meta/terra.md·meta/experiments.json','규모정책과 공개식을 일치시키고 잔류단독축소·만기까지무조건대기를 폐기했다. 카펫종료는 하네스로그를 쓰는 다음Terra가 한다.')
for n in [1,2,3,4,5,7,8,9,10,11,12,13,14,15,16,17,18,19,20]:ev(BASE+'-measurement.jsonl',n,'전략·숙성손익·규모조건·동연령 포럼 비교')
ev(BASE+'-scale.jsonl',1,'실제단위 회전: 고가 새매입표본0, 동일단위잔류는 확대조건 미달')
for n in range(1,14):ev(BASE+'-history.jsonl',n,'과거Terra 확대조건 재조회 및 바이어전후1시간 지급률; 초기목표는규모진입후 기준의 회고조회')
for n in [1,2]:ev(BASE+'-trials.jsonl',n,'실제입고원가로 독립 재시험 손익보완, 보관료는 시간비례 추정')
for n,line in enumerate(Path(BASE+'-tests-1.txt').read_text().splitlines(),1):
 if line.startswith('Ran 437 tests'):ev(BASE+'-tests-1.txt',n,'지정venv 전체테스트 통과',line)
options=[dict(direction='현행 규모전략과 흑자 G03 복원',expected_profit='12시간 book +2,000만~3,000만원은 직전12.18시간 +26,279,819 추세 시나리오. 추가 G03 0~2건×294,673=0~59만원은 기본추세와 중복될 수 있다.',risk='미숙성533건·원가결측2건, 환불·보관료와 수요변화. 15M총상한·파산가드 유지.',test='G03만1→2, 첫6시간 판매의숙성≥5건 순기여>0과12시간book비하락 판정.'),dict(direction='검증 고가소파 집중 확대',expected_profit='기본추세 외0~231만원=추가0~6건×G8 숙성385,720. 선두G8 166건 대 우리18건, 경쟁원가 비공개.',risk='현재 우선차단0/0·새매입표본0·동일4소파잔류로 확대조건 실패. 보증환불·서지 부담.',test='세조건이 모두 맞을때만15M→22.5M 및 판매입증SKU목표한단계; 지금은보류.'),dict(direction='포럼 미충족 수요 연결',expected_profit='직접추가0~5만원은0~1판매×카펫비교기여51,233의 가정. 포럼가격동향38/구매기록56 동연령중앙값은모두.487로득표증가미확정.',risk='구합니다 글은 실제주문·acceptable SKU 증명이 아니며 식품기한·미판매비용이 있다.',test='읽기전용 forum_demand로 실제요청2개 연결후 기존가드안1줄1단위 제안만 시험.')]
gap=dict(missing='G03 복원 및 다음 규모확대의 독립효과와 일부 판매의 6시간 숙성 결과. 고가 최근두창 새매입표본은0이라 지급률을 계산할 수 없다.',measurement='이번입고·실제정산·더이른관측출고를 연결하고 우선상한차단0/0·동일소파4단위잔류를 계산했다. scorecard에 반복조회 추가. G03숙성6건+1,768,035를 확인해1→2 복원.',judge_line=dict(metric='G03 복원후 첫6시간 판매의 숙성 순기여 및12시간book변화',direction='above',threshold='숙성 순기여>0 및 book 변화>=0',min_sample='숙성판매5건; 표본미달은 효과미확정으로 종료하고 추가확대중지',expires='2026-11-06 09:49 KST',undo='총상한을 줄이지 않는다. G03 최근숙성10건 순기여<=0이면 목표2→1만 복원; 미달표본은 추가확대중지.'))
out=dict(changed=True,hypothesis='흑자SKU를 잔류만으로 줄이지 않고 규모조건을 실제단위로 매시간 측정하면 확대기회를 놓치거나 잘못 축소하는 판단을 줄일 수 있다. 수요글과 정산을 연결하면 소량탐색의 근거가 개선된다.',change_summary='G03 목표1→2복원. 두창회전 자동측정·공식혼합 감지·읽기전용 forum_demand와회귀시험 추가. 전략·Terra지침·실험큐 재작성, G6가격인하 대기안 제거. 전체변경과도구 평가 저장.',target_metric='Everyone Players·book, SKU숙성순기여, 확대조건 측정완료율, 수요연결 정확성',expected='12시간 book +2,000만~3,000만원은 추세시나리오이며1위보장은 없다. G03 복원 추가0~59만원가정. 공식혼합 오표시1건수정, 두창측정 자동화;437테스트통과.',evidence=evidence,evidence_gap=gap,summary='61커밋과10Terra기록을 전수평가했다. 실제보드 Players1.279→1.380, book+26,279,819이나 선두격차415,870,649. G03축소를폐기하고15M상한은조건미달로유지했다. 437테스트통과. 커밋·push·PR 없음.',strategy=dict(options=options,chosen='현행규모전략 유지. 지금대안의수익우위가 입증되지 않았고 상한확대세조건도미달이다. 흑자G03복원과조건자동측정을 먼저 수행한다.',experiment='현재카펫은다음Terra에서숙성성공done. 그다음scale-qualified-growth를세조건충족시에만실행해12시간book비하락·숙성5건순기여양수로판정. 다음forum-demand-context-one-line은12시간실제요청2개정확연결·오인용0으로판정. 동시에시작하지않는다.'),grades=grades,keep_or_drop=keeps)
# Counts come from audited inputs, not a guessed summary.
nc=sum(x['kind']=='commit' for x in audit);nt=sum(x['kind']=='terra' for x in audit)
out['summary']=out['summary'].replace('61커밋과10Terra기록',f'{nc}커밋과{nt}Terra기록')
Path(BASE+'-review.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'commits':nc,'terra':nt,'grades':len(grades),'keep_or_drop':len(keeps),'evidence':len(evidence)},ensure_ascii=False))
