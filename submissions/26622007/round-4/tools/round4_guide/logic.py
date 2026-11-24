import httpx

def run(args, kit):
    kw=args.get('keyword','checkout')
    r=httpx.get(kit.market.rstrip('/')+'/ROUND4.md',timeout=15)
    text=r.text
    i=text.lower().find(kw.lower())
    if i<0: return {'status':r.status_code,'found':False,'length':len(text),'tail':text[-7000:]}
    return {'status':r.status_code,'found':True,'length':len(text),'start':max(0,i-1800),'text':text[max(0,i-1800):min(len(text),i+6500)]}
