"""Payment attribution across reuse of a timed-out unit (synthetic inventory)."""
import unittest
from unittest.mock import patch

from shop_stock import StockBook, PAY_SECONDS


class LateReuseTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.clock = patch('shop_stock.time.time', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.inventory = [{'unit': 'u-000000000001', 'sku': 'G00000001', 'grade': 'A'},
                          {'unit': 'u-000000000002', 'sku': 'G00000001', 'grade': 'A'}]
        self.unit = self.inventory[0]['unit']
        self.other = self.inventory[1]['unit']
        self.book = StockBook(load=lambda: {'round': 3, 'units': self.inventory})
        self.events = []
        self.book.on_paid = lambda info: self.events.append(('paid', info['task']))
        self.book.on_unpaid = lambda info, why: self.events.append(('unpaid', info['task']))

    def agree(self, task, unit=None):
        self.book.agree(unit or self.unit, {'task': task, 'sku': 'G00000001', 'price': 100})

    def timeout(self):
        self.now += PAY_SECONDS + 1
        self.book.settle()

    def remove(self, unit=None):
        self.inventory[:] = [u for u in self.inventory if u['unit'] != (unit or self.unit)]

    def test_new_sale_does_not_pay_previous_timed_out_task(self):
        self.agree('A')
        self.timeout()
        self.agree('B')
        self.book.mark_sold(self.unit)
        self.remove()
        self.book.settle()
        self.book.mark_sold(self.unit)
        self.book.settle()
        self.assertEqual(self.events, [('unpaid', 'A'), ('paid', 'B')])


if __name__ == '__main__':
    unittest.main()
