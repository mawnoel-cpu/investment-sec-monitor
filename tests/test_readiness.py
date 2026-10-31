import unittest
import ft_trade_desk as desk


class ReadinessTests(unittest.TestCase):
    def test_gate_has_no_permissive_default(self):
        gate = desk.readiness_gate("pending", "complete", "fresh", "qualified", "act")
        self.assertLess(gate.index("pending"), gate.index("complete"))
        self.assertLess(gate.index("complete"), gate.index("fresh"))
        self.assertIn("UNRESOLVED", gate)
        self.assertIn("DO NOT RESUBMIT", gate)
        self.assertIn('IF(act="HOLD","HOLD—review completed"', gate)

    def test_only_live_panel_is_written(self):
        requests = desk.readiness_requests(123)
        self.assertEqual(len(requests), 72)
        for request in requests:
            cell = request["updateCells"]
            self.assertEqual(cell["fields"], "userEnteredValue")
            self.assertEqual(cell["start"]["sheetId"], 123)
            self.assertTrue(24 <= cell["start"]["rowIndex"] < 60)
            self.assertTrue(0 <= cell["start"]["columnIndex"] < 2)

    def test_holding_gate_is_independent_of_candidate_research(self):
        requests = desk.readiness_requests(123)
        holding = [r["updateCells"] for r in requests
                   if 34 <= r["updateCells"]["start"]["rowIndex"] < 53]
        for cell in holding:
            text = str(cell)
            self.assertNotIn("CANDIDATE POOL", text)
            self.assertNotIn("ROTATION BENCH", text)
        formula = holding[1]["rows"][0]["values"][0]["userEnteredValue"]["formulaValue"]
        for mandatory in ("Reviews!$N", "Reviews!$X", "Decision History", "Trade Log", "cap<=NOW()", "VERIFIED", "ABS("):
            self.assertIn(mandatory, formula)

    def test_existing_job_consumes_live_panel(self):
        import inspect
        self.assertIn('sync_readiness(book)', inspect.getsource(desk.sync_views))
        self.assertIn('readiness_requests(sheet.id)', inspect.getsource(desk.sync_readiness))

    def test_formula_install_must_read_back(self):
        class Book:
            id = 123
            def worksheet(self, name):
                return self
            def batch_update(self, body):
                self.body = body
            def get(self, address, value_render_option):
                self.read = (address, value_render_option)
                cells = self.body["requests"]
                return [[next(iter(cells[i+c]["updateCells"]["rows"][0]["values"][0]
                                  ["userEnteredValue"].values())) for c in range(2)]
                        for i in range(0, len(cells), 2)]
        book = Book()
        desk.sync_readiness(book)
        self.assertEqual(book.read, ("A25:B60", "FORMULA"))
        book.get = lambda *args, **kwargs: []
        with self.assertRaisesRegex(RuntimeError, "readback mismatch"):
            desk.sync_readiness(book)

    def test_formulas_have_balanced_quotes_and_parentheses(self):
        for request in desk.readiness_requests(123):
            value = request["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]
            if "formulaValue" not in value:
                continue
            quoted, depth = False, 0
            for char in value["formulaValue"]:
                if char == '"':
                    quoted = not quoted
                elif not quoted:
                    depth += (char == "(") - (char == ")")
                    self.assertGreaterEqual(depth, 0)
            self.assertFalse(quoted)
            self.assertEqual(depth, 0)

    def test_weekend_finance_uses_actual_clock_and_rejects_future(self):
        from datetime import datetime, timedelta
        import ft_price_writer as prices
        import ft_financial_stress as finance
        import ft_pipeline_health as health
        saturday = datetime(2026,10,31,11,40,tzinfo=prices.TORONTO)
        receipt = [["FINANCING CALCULATION","VERIFIED"],
                   ["FINANCING AS OF",prices.serial(saturday-timedelta(minutes=5))]]
        self.assertEqual(finance.health(saturday, receipt, [], []), [])
        self.assertTrue(finance.health(health.decision_clock(saturday), receipt, [], []))
        receipt[1][1] = prices.serial(saturday+timedelta(minutes=1))
        self.assertTrue(finance.health(saturday, receipt, [], []))

    def test_health_main_passes_actual_clock_to_financing(self):
        import ast
        import inspect
        import ft_pipeline_health as health
        tree = ast.parse(inspect.getsource(health.main))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "health"
                 and isinstance(node.func.value, ast.Name) and node.func.value.id == "finance"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0].id, "checked_now")

    def test_manual_weekend_review_does_not_replace_weekday_deadline(self):
        requests=desk.readiness_requests(1)
        formulas={r["updateCells"]["start"]["rowIndex"]+1:
                  r["updateCells"]["rows"][0]["values"][0]["userEnteredValue"].get("formulaValue","")
                  for r in requests if r["updateCells"]["start"]["columnIndex"]==1}
        self.assertIn(desk.readiness_review_day(),formulas[27])
        self.assertIn('manual weekend refresh',formulas[27])
        self.assertNotIn(desk.readiness_review_day(),formulas[32])
        self.assertIn('on_time_scheduled_proof',formulas[32])
        self.assertIn('WEEKDAY(TODAY(),2)>5',desk.readiness_review_day())
        self.assertIn('COUNTIF(Reviews!',desk.readiness_review_day())


if __name__ == "__main__":
    unittest.main()
