import json,subprocess,time
from pathlib import Path
base='meta/evidence/astra-1110-143630';end=time.time()
rows={r['seq']:r for r in map(json.loads,open('.market/ledger_rows.jsonl')) if r.get('k')=='settle' and 1794287445<=r.get('ts',0)<=end}
mine=json.load(open('.market/loss_state.json'))['mine']
def window(lo,hi):
 rs=[r for r in rows.values() if lo<=r['ts']<hi]
 return dict(hours=round((hi-lo)/3600,4),paid=sum(str(r['seq']) in mine for r in rs),buyer_paid=sum(r.get('buyer','').lower()=='nocha12' for r in rs),market_paid=len(rs),mature_net=None,mature_paid=0)
r=[]
for l in subprocess.check_output(['git','log','--reverse','--since=2026-11-10T14:36:43+09:00','--format=%h%x09%ct%x09%s','--','.'],text=True).splitlines():
 sha,t,title=l.split('\t',2);t=int(t)
 r.append(dict(commit=sha,title=title,measurement_end=end,before=window(t-1800,t),after=window(t,min(t+1800,end))))
Path(base+'-concurrent.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in r))
p=Path(base+'-review.json');d=json.loads(p.read_text())
for i,x in enumerate(r,1):
 a=x['before'];b=x['after'];ref=base+'-concurrent.jsonl:'+str(i)
 d['grades'].append(dict(change=x['commit']+' '+x['title'],grade='unclear',reason=f'{ref} 팩 마감 뒤 동시 반영된 기본OFF 구현. 전후 정산{a["paid"]}→{b["paid"]}, 구매결제{a["buyer_paid"]}→{b["buyer_paid"]}, 숙성0건으로 순기여null. 이번 원장 시간창 조회와 최종1882검사 통과는 기능 검증이며 운영효과가 아니다. 처리 노출/대조군이 결측. 11/11 02:36 처리·대조 각30건/6h 순기여와 추가Players>0 전에는 확대 금지, 표본미달은 OFF유지.'))
 d['keep_or_drop'].append(dict(item=x['commit']+' 동시 반영 buyer_switch_write/final_m1',decision='keep',reason='유한창/동시작성/이력/판독의 최종 수정본 유지. 사람의 기본OFF를 켜지 않으며 원래 시험 종료·철회조건을 따른다.'))
 d['evidence'].append(dict(ref=ref,excerpt=json.dumps(x,ensure_ascii=False),meaning='팩 이후 별도 시각의 전후 관측; 앞의 고정마감 집계와 합치지 않는다.'))
p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print('additional',len(r),'grades',len(d['grades']))
