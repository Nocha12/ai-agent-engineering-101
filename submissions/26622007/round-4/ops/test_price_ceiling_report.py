import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPORT = Path(__file__).with_name("price_ceiling_report.py")
FIXTURE = '''
def build_rows(qty=1, batch_size=1, hold_hours=1, unsold=None):
    items = [("apple", "사과", 0), ("pear", "배<script>", -10)]
    if hold_hours == 24:
        items.reverse()
    return [dict(sku=sku, name=name, kind="food", list_price=1500, sell=1400,
                 cost=800, sell_source="거래 <표본>", cost_source="원가 & 추정",
                 unsold=0.2 if unsold is None else unsold,
                 ceiling=offset + (1000-hold_hours if unsold is None else int(900*(1-unsold))),
                 floor=1100, profit=-12, risk="기한 경과", conservative_ceiling=None,
                 conservative_floor=None)
            for sku, name, offset in items]
'''


class ReportTest(unittest.TestCase):
    def test_isolated_cli_writes_all_products_and_scenario_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ops = root / "round-4" / "ops"
            ops.mkdir(parents=True)
            shutil.copyfile(REPORT, ops / REPORT.name)
            (ops / "price_ceiling.py").write_text(FIXTURE, encoding="utf-8")
            subprocess.run([sys.executable, "-I", "-B", str(ops / REPORT.name)],
                           cwd=root, check=True, capture_output=True, text=True)
            html = (ops.parent / "logs" / "price-ceiling.html").read_text(encoding="utf-8")
        for text in ['lang="ko"', '<caption>', 'scope="row"', 'scope="col"',
                     '사과', '배&lt;script&gt;', '거래 &lt;표본&gt;', '원가 &amp; 추정',
                     '999', '976', '675', '불가', '보장하지', '미판매', '보유']:
            with self.subTest(text=text):
                self.assertIn(text, html)
        self.assertNotIn('<script', html)
        tables = [ET.fromstring('<table>' + fragment.split('</table>', 1)[0] + '</table>')
                  for fragment in html.split('<table>')[1:]]
        sensitivity, = [table for table in tables if '조건별 매입 상한' in table.findtext('caption')]
        self.assertEqual([[cell.text for cell in row] for row in sensitivity.findall('tbody/tr')],
                         [['사과 (apple)', '999', '976', '900', '675'],
                          ['배<script> (pear)', '989', '966', '890', '665']])


if __name__ == "__main__":
    unittest.main()
