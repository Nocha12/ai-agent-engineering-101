import json

def run(args, kit):
    card={
      'name':'Nocha Furniture',
      'description':'노차가 정직하게 운영하는 가구·생활용품점입니다. 현재 재고와 등급을 확인한 뒤 정확한 가격과 보증 조건을 안내합니다. 재고가 없으면 매입 여부를 확인해 답변드립니다.',
      'version':'4.0.0',
      'provider':{'organization':'Nocha Furniture'},
      'supportedInterfaces':[{'url':kit.shop_url,'protocolBinding':'A2A','protocolVersion':'1.0'}],
      'defaultInputModes':['text/plain','application/json'],
      'defaultOutputModes':['text/plain','application/json'],
      'skills':[{'id':'product_inquiry','name':'상품 문의','description':'상품명 또는 SKU를 알려주시면 재고, 현재 등급, 가격 및 보증 조건을 확인해 안내합니다. 재고가 없으면 판매 가능하다고 약속하지 않습니다.','tags':['shop','inventory','sales'],'examples':[],'inputModes':['text/plain'],'outputModes':['text/plain']}]
    }
    with open('card.json','w',encoding='utf-8') as f: json.dump(card,f,ensure_ascii=False)
    return {'written':True,'name':card['name']}
