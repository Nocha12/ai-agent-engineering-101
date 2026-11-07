import tempfile, unittest
from pathlib import Path
from seller_late_concession import LateConcession

class LateFloorTests(unittest.TestCase):
    def test_only_round_three_onwards_reduces_remaining_margin(self):
        with tempfile.TemporaryDirectory() as root:
            p = LateConcession(root)
            d = dict(rounds=2, reserve=100, ask=180, late_concession=dict(arm="B", id="adopted-v1"))
            self.assertEqual(p.floor(d, 160),160)
            d["rounds"]=3
            self.assertEqual(p.floor(d,160),154)
            self.assertEqual(p.floor(d,100),100)
            d["ask"]=120
            self.assertEqual(p.floor(d,120),118)
    def test_control_and_missing_assignment_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            p=LateConcession(root)
            for a in ({},dict(arm="A",id="adopted-v1")):
                d=dict(rounds=5,reserve=100,ask=180,late_concession=a)
                self.assertEqual(p.floor(d,160),160)
if __name__=="__main__":unittest.main()
