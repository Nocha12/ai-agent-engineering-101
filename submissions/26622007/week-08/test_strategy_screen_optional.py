"""strategy_screen is a private module that the public branch does not carry (cdcbb01b). Its readers say so, not crash."""
import importlib.util
import os
import shutil
import tempfile
import unittest

import agent_tools
import scorecard

HERE = os.path.dirname(os.path.abspath(__file__))
ABSENT = importlib.util.find_spec("strategy_screen") is None


class StrategyScreenOptionalTest(unittest.TestCase):
    @unittest.skipUnless(ABSENT, "strategy_screen is here: the scorecard carries its real screen")
    def test_the_scorecard_says_the_screen_is_not_here(self):
        with tempfile.TemporaryDirectory() as root:
            out = scorecard.market_opportunities(root)
        self.assertIs(out["available"], False)
        self.assertEqual(out["candidates"], [])
        self.assertIn("strategy_screen", out["basis"])

    @unittest.skipUnless(ABSENT, "strategy_screen is here: the tool answers with its real screen")
    @unittest.skipUnless(shutil.which("sandbox-exec"), "tools do not run without sandbox-exec")
    def test_the_tool_answers_not_available_instead_of_a_traceback(self):
        with tempfile.TemporaryDirectory() as root:
            md = os.path.join(root, ".market")
            os.mkdir(md)
            out = agent_tools.execute("market_opportunity", {"sku": "G"}, md, folder=os.path.join(HERE, "tools"))
        self.assertNotIn("error", out)
        self.assertIs(out["available"], False)
        self.assertEqual(out["candidates"], [])

    @unittest.skipIf(ABSENT, "strategy_screen is a private module that is not on this branch")
    def test_the_scorecard_carries_the_screen_when_the_module_is_here(self):
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, ".market"))
            out = scorecard.market_opportunities(root)
        self.assertEqual(out["candidates"], [])
        self.assertNotIn("available", out)


if __name__ == "__main__":
    unittest.main()
