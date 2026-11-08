import json,pathlib,sys
sys.path.insert(0,str(pathlib.Path.cwd()))
from meta_harness import locate_evidence,verify_evidence
P=pathlib.Path;prefix='meta/evidence/astra-1109-011816';ap=prefix+'-audit.jsonl'
rows=[json.loads(l) for l in open(ap)];grades=[];keeps=[];evidence=[]
def ev(path,n,excerpt,meaning):evidence.append(dict(ref=f'{path}:{n}',excerpt=excerpt,meaning=meaning))
for i,r in enumerate(rows,1):
 if r['kind']!='commit':continue
 sha=r['commit'];b=r['before'];a=r['after'];title=r['message'].splitlines()[0]
 context=f"전후30분 상점결제 {b['shop_paid']}→{a['shop_paid']}, 구매결제 {b['buyer_paid']}→{a['buyer_paid']}, 시장결제 {b['market_paid']}→{a['market_paid']}. {ap}:{i}. "
 if title.startswith(('chore','docs','merge','Merge','revert')) or 'wip(' in title:
  grade='no_effect';reason=context+'기록·병합 또는 거절 후 복원이다. 이 커밋 단독의 새 운영 효과를 계상하지 않는다.'
 elif sha=='041b137d':
  grade='worked';reason=context+'기존 경로 검증 실패27→0 및 이번1152검사 통과. 테스트 경로 수정은 효과 확인; 공급 지침 제거의 경제 효과는 동시 변경 때문에 분리 불가.'
 elif sha=='46e061fc':
  grade='unclear';reason=context+'진행 중 포럼 수요 실험의 글3개·유효후보1개 증거가 부족. 현재 forum 판단을 측정했으며 07:46 후보>=1/오연결0, 미달시 해당 지침 제거.'
 elif sha=='2327f1c5':
  grade='worked';reason=context+'완전 일치 OK 인용은 이번 locate/verify 검사에서 통과. 다만 뒤 Terra의 틀린 행번호는 여전히 거절되므로 운영 적용 실패 전체를 해결한 것은 아니다.'
 else:
  grade='unclear';reason=context+'동시 수정·경쟁 수요 및 6h 미숙성의 영향 분리 불가. 변경별 노출/지급을 연결해 11/09 13:18까지 동일시각 적격주문 각30건에서 결제전환 개선·숙성Saved 비악화를 판정한다. 표본 부족시 미확정 유지, 사람 변경은 원래 철회조건 성립시만 복원.'
 grades.append(dict(change=sha+' '+title,grade=grade,reason=reason))
 ev(ap,i,json.dumps({'before':b},ensure_ascii=False)[1:-1], '변경별 전30분 실제 원장 집계; 같은 행 after와 비교하며 인과효과는 별도 판정')
 ev(ap,i,json.dumps({'after':a},ensure_ascii=False)[1:-1], '변경별 후30분 실제 원장 집계; late cohort는 미숙성')
 keeps.append(dict(item=sha+' '+', '.join(r['files']),decision='keep',reason='현재 적용 상태와 거절·복원 이력을 유지한다. '+('수익 개선은 미확정이며 원래 철회조건 충족 증거가 없다.' if grade=='unclear' else reason.split('. ')[-1])))
for i,l in enumerate(open('meta/changes.jsonl'),1):
 r=json.loads(l)
 if r['time']<'2026-11-08T12:33':continue
 a=r.get('answer') or {};cy=r['cycle'];status=r['status']
 if status=='rejected':g='harmful';why='검증·인용 또는 시간초과로 변경이 되돌려졌다. 경제 효과는 no_effect이며 반복 적용 실패는 시스템 품질 감점이다.'
 else:g='unclear';why='규칙·Players 점검은 맞고 Luna 상한을 되살리지 않았다. 총상한1B 비구속 판단은 맞지만 SKU별 세 증가조건·기존 실험 효용의 직접 측정은 부족하다.'
 grades.append(dict(change=cy+' 전략·검증 점검',grade=g,reason=f'{why} meta/changes.jsonl:{i}. 이번 두 창 G8 3→3 판매, G24 3→1 판매를 측정했다. 다음 두 승인 주기에도 근거 누락이면 점검 실패; 신규 패치 없어 패치 대응속도는 평가 불가.'))
 ev('meta/changes.jsonl',i,'"status": "'+status+'"','실제 수용 여부와 제안된 변경을 분리한다')
# Every installed/generated tool, including absent remnants, gets an explicit disposition.
pack=json.load(open(prefix+'.json'));tools=set(pack['tools'])|set(pack['shop']['tools_status']['available'])
for name in sorted(tools):
 stats=next((r for r in rows if r['kind']=='tool' and r['tool']==name),{'calls':0,'failures':0})
 drop=name=='auto_market_research_context';active=name in pack['shop']['tools_status']['available']
 reason=f"검토구간 호출{stats['calls']}/실패{stats['failures']}; "
 reason+=('상품1개·결제1건 fixture가 결제0건을 반환하고 SKU를 잃음. 원본 보존 후 manifest/tool 삭제, 전용 테스트 없음.' if drop else ('공통 회귀검사 통과; 호출 성공만으로 수익효과를 인정하지 않는다.' if active else '이미 제거된 manifest 상태를 유지한다. 캐시 디렉터리를 도구로 다시 활성화하지 않는다.'))
 keeps.append(dict(item='tools/'+name,decision='drop' if drop else 'keep',reason=reason))
grades.append(dict(change='auto_market_research_context 의미검증',grade='harmful',reason='정상 종료0이지만 결제1→0, SKU키 소실. 신규 공급 근거로 부적절하여 제거했다. '+prefix+'-tool-probe.json:4'))
keeps += [dict(item='pricing.json 전체 설정 및 보호한도',decision='keep',reason='총상한1B 비구속. G8 숙성원가 누락·G24 두창 판매1/3으로 확대 세조건 미완비. 총량축소·거절목록·Luna제한 변경 없음.'),dict(item='meta_harness.locate_evidence + test_evidence_locator.py',decision='keep',reason='유일한 완전일치 행만 생성하고 기존 검증 유지; 새3검사 및 전체1152검사 통과.')]
evidence.append(locate_evidence(prefix+'-tests.txt','Ran 1152 tests in 119.615s','지정 절대경로 venv 전체검사'))
evidence.append(locate_evidence(prefix+'-tests.txt','OK','전체검사 통과'))
probe=P(prefix+'-tool-probe.json').read_text().splitlines()
for i,l in enumerate(probe,1):
 if 'stdout' in l:ev(prefix+'-tool-probe.json',i,l.strip(),'결제 연결 누락과 SKU키 소실 재현')
for file,n in [(prefix+'-supplement-v2.jsonl',2),(prefix+'-supplement-v2.jsonl',3),(prefix+'-supplement-v2.jsonl',6),(ap,1),(ap,81),(ap,82)]:
 line=P(file).read_text().splitlines()[n-1];ev(file,n,line,'시장·공급 관측. 경쟁원가와 인과효과는 미식별')
review=dict(cycle='astra-1109-011816',grades=grades,keep_or_drop=keeps,evidence=evidence)
P(prefix+'-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
problems=verify_evidence(evidence)
P(prefix+'-citation-check.json').write_text(json.dumps(dict(citations=len(evidence),problems=problems),ensure_ascii=False,indent=2)+'\n')
print('grades',len(grades),'keep/drop',len(keeps),'citations',len(evidence),'problems',problems)
