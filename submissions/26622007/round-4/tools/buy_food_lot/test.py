def test(run,kit):
 assert run({'sku':'x','qty':1},kit)['ok'] is False
 assert run({'sku':'x','qty':2},kit)['ok'] is False
 if kit.testing: assert 'testing mode' in run({'sku':'x','qty':2},kit)['error']
