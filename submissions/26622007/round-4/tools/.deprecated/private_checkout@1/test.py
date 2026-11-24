def test(run, kit):
    assert run({'op':'bad'},kit)['ok'] is False
    assert run({'op':'create','sku':'x','price':0,'task':'t','unit':'u','grade':'A'},kit)['ok'] is False
    assert run({'op':'settle','sku':'x','price':1000,'task':'t','unit':'u','grade':'A','order':'o'},kit)['ok'] is False
    out=run({'op':'create','sku':'x','price':1000,'task':'t','unit':'u','grade':'A','warranty_hours':24},kit)
    if kit.testing: assert out['ok'] is False or isinstance(out.get('checkout_jwt'),str)
