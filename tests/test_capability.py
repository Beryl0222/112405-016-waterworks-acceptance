"""业务能力核对测试：解析、失效、确认、签认、撤回、交接后事项。"""
import json
import tempfile
import unittest
from pathlib import Path

from hecha import model as M
from hecha import signoff as signoff_mod
from hecha import store
from hecha.evaluate import (build_factbase, check_claims, check_confirmation,
                            evaluate_segment)
from hecha.handover import build_states

ROOT = Path(__file__).parents[1]
CASE = ROOT / "data" / "case.json"


def load(journal_entries=None):
    if journal_entries is None:
        return store.load_view(CASE, None)
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"entries": journal_entries}, tmp, ensure_ascii=False)
    tmp.close()
    return store.load_view(CASE, tmp.name)


def fb_of(view):
    return build_factbase(view)


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.fb = fb_of(load())

    def test_old_number_maps_to_current_segments(self):
        """重编号不改变原试验范围：WA-T1的旧号L2A映射到L2A1、L2A2。"""
        t1 = next(p for p in self.fb.pressure if p.record_id == "WA-T1")
        self.assertEqual(t1.segments, frozenset({"L2A1", "L2A2"}))
        self.assertEqual(t1.date, (7, 31))
        self.assertEqual(t1.excluded, frozenset({"B"}))  # 摘要不得删掉除外
        self.assertIn(("L2A1", "J50"), t1.interfaces)
        self.assertIn(("L2A2", "J50"), t1.interfaces)

    def test_retest_scope_and_not_covered(self):
        c1 = next(p for p in self.fb.pressure if p.record_id == "WA-C1")
        self.assertEqual(c1.segments, frozenset({"L2A1"}))
        self.assertEqual(c1.interfaces, frozenset({("L2A1", "J50")}))
        self.assertIn("L2A2", c1.not_covered[0][1])  # 未测L2A2侧接口

    def test_correction_makes_flush_failed(self):
        f2 = next(f for f in self.fb.flush if f.record_id == "WA-F2")
        self.assertTrue(f2.interrupted)
        self.assertEqual(f2.longest_continuous_min, 13)  # 累计25不是连续25

    def test_confirmation_parsed(self):
        r1 = next(c for c in self.fb.confirmations if c.record_id == "WA-R1")
        self.assertEqual(r1.target, "WA-F1")
        self.assertEqual(r1.value, 22)
        self.assertEqual(r1.source, "计时原表")
        self.assertEqual(r1.scope, frozenset({"L2A1"}))

    def test_hearsay_is_not_evidence(self):
        self.assertEqual(len(self.fb.claims), 1)
        self.assertEqual(self.fb.claims[0].record_id, "WA-N1")

    def test_unrecorded_duration_stays_missing(self):
        f1 = next(f for f in self.fb.flush if f.record_id == "WA-F1")
        self.assertIsNone(f1.duration_min)  # 未记时间不能猜
        self.assertTrue(f1.duration_unrecorded)


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.view = load()
        self.fb = fb_of(self.view)

    def test_l2a1_all_satisfied(self):
        ev = evaluate_segment("L2A1", self.view, self.fb)
        self.assertTrue(ev.ok, ev.verdict)

    def test_l2a2_pressure_missing_j100_after_new_work(self):
        ev = evaluate_segment("L2A2", self.view, self.fb)
        ac1 = ev.criteria[0]
        self.assertEqual(ac1.status, M.NO_EVIDENCE)
        text = "；".join(ac1.missing)
        self.assertIn("J100", text)
        self.assertIn("WA-E1", text)  # 新施工后局部重验

    def test_cut_reconnect_invalidates_joint_evidence_only(self):
        """切断重接只让局部证据失效：WA-T1对J50接口失效，对管段仍有效。"""
        ev = evaluate_segment("L2A2", self.view, self.fb)
        ac1 = ev.criteria[0]
        j50_evidence = [e for e in ac1.evidence if "J50" in e.note]
        self.assertTrue(j50_evidence)
        self.assertTrue(all(e.record_id == "WA-T2" for e in j50_evidence))
        pipe = [e for e in ac1.evidence if "管段" in e.note]
        self.assertIn("WA-T1", {e.record_id for e in pipe})

    def test_no_stitching_across_joint_modification(self):
        """WA-T1与WA-C1不能拼成一次完整试验：L2A2侧接口只能靠WA-T2。"""
        ev = evaluate_segment("L2A2", self.view, self.fb)
        ids = {e.record_id for c in ev.criteria for e in c.evidence}
        self.assertNotIn("WA-C1", ids)

    def test_l2a2_flushing_explicitly_failed(self):
        ev = evaluate_segment("L2A2", self.view, self.fb)
        ac2 = ev.criteria[1]
        self.assertEqual(ac2.status, M.FAILED)  # 明确不合格，不是参数不全
        self.assertIn("13", "；".join(ac2.missing))

    def test_l2a1_flushing_satisfied_by_sourced_confirmation(self):
        ev = evaluate_segment("L2A1", self.view, self.fb)
        ac2 = ev.criteria[1]
        self.assertEqual(ac2.status, M.OK)
        ids = {e.record_id for e in ac2.evidence}
        self.assertIn("WA-F1", ids)
        self.assertIn("WA-R1", ids)

    def test_incomplete_vs_failed_are_different(self):
        """参数不全与明确不合格给不同处理。"""
        view = load([{"type": "withdrawal", "record": "WA-R1",
                      "reason": "测试"}])
        fb = fb_of(view)
        ac2_l1 = evaluate_segment("L2A1", view, fb).criteria[1]
        ac2_l2 = evaluate_segment("L2A2", view, fb).criteria[1]
        self.assertEqual(ac2_l1.status, M.INCOMPLETE)   # 可确认补救
        self.assertEqual(ac2_l2.status, M.FAILED)       # 只能重新冲洗
        self.assertIn("确认", ac2_l1.handling)
        self.assertIn("重新", ac2_l2.handling)

    def test_branch_b_never_covered(self):
        ev = evaluate_segment("B", self.view, self.fb)
        ac1 = ev.criteria[0]
        self.assertEqual(ac1.status, M.NO_EVIDENCE)
        self.assertIn("不含", "；".join(ac1.missing))  # WA-T1明确排除支管B

    def test_isolation_gate(self):
        l1 = evaluate_segment("L2A1", self.view, self.fb).criteria[2]
        l2 = evaluate_segment("L2A2", self.view, self.fb).criteria[2]
        self.assertEqual(l1.status, M.OK)           # V0有效
        self.assertEqual(l2.status, M.NO_EVIDENCE)  # J100与B共用，无隔离

    def test_claim_rejected(self):
        checks = check_claims(self.view, self.fb)
        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0].ok)  # “全线无渗漏”不被证据支持

    def test_plan_is_not_evidence(self):
        view = load([{"type": "record", "record": {
            "id": "WA-P1", "kind": "pressure-test",
            "text": "九月十日计划对B试压0.8MPa连续30分钟。"}}])
        fb = fb_of(view)
        ev = evaluate_segment("B", view, fb)
        self.assertEqual(ev.criteria[0].status, M.NO_EVIDENCE)


class ConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.view = load()
        self.fb = fb_of(self.view)

    def confirm(self, **kw):
        conf = M.ConfirmationFact(
            kw.get("rid", "CF-X"), kw["target"], kw["param"], kw["value"],
            kw.get("unit", "分钟"), kw.get("source"),
            frozenset(kw.get("scope", [])), "测试确认")
        return check_confirmation(conf, self.fb)

    def test_reject_without_source(self):
        conf = self.confirm(target="WA-F1", param=M.PARAM_FLUSH_DURATION,
                            value=25, source=None)
        self.assertTrue(conf.rejected)
        self.assertIn("来源", "；".join(conf.rejected))

    def test_reject_confirming_failed_record(self):
        """明确不合格不能确认补救。"""
        conf = self.confirm(target="WA-F2", param=M.PARAM_FLUSH_DURATION,
                            value=25, source="某表")
        self.assertTrue(conf.rejected)
        self.assertIn("不合格", "；".join(conf.rejected))

    def test_reject_duplicate_confirmation(self):
        conf = self.confirm(target="WA-F1", param=M.PARAM_FLUSH_DURATION,
                            value=20, source="另一份表")
        self.assertTrue(conf.rejected)  # WA-R1已确认

    def test_reject_scope_beyond_record(self):
        conf = self.confirm(target="WA-F1", param=M.PARAM_FLUSH_DURATION,
                            value=22, source="表", scope=["L2A2"])
        self.assertTrue(conf.rejected)

    def test_confirmation_scope_limited(self):
        """WA-R1仅适用L2A1，不能用于L2A2。"""
        ac2_l2 = evaluate_segment("L2A2", self.view, self.fb).criteria[1]
        self.assertEqual(ac2_l2.status, M.FAILED)


class SignoffTest(unittest.TestCase):
    def setUp(self):
        self.view = load()
        self.fb = fb_of(self.view)

    def test_l2a2_refused_with_reasons(self):
        result = signoff_mod.attempt_signoff(self.view, self.fb, ["L2A2"])
        self.assertFalse(result.ok)
        text = "；".join(result.reasons)
        self.assertIn("L2A2", text)

    def test_batch_refused_until_fixed(self):
        result = signoff_mod.attempt_signoff(self.view, self.fb, ["L2A2", "B"])
        self.assertFalse(result.ok)

    def test_batch_accepted_after_new_evidence(self):
        """补齐局部重验、重新冲洗与B的试验后，同批可签认。"""
        view = load([
            {"type": "record", "record": {
                "id": "WA-T3", "kind": "pressure-test",
                "text": "八月六日J100两侧接口及B试压0.8MPa连续30分钟，无渗漏。"}},
            {"type": "record", "record": {
                "id": "WA-F4", "kind": "flushing",
                "text": "八月六日L2A2每秒6升连续冲洗25分钟。"}},
            {"type": "record", "record": {
                "id": "WA-F5", "kind": "flushing",
                "text": "八月六日B每秒6升连续冲洗25分钟。"}},
        ])
        fb = fb_of(view)
        result = signoff_mod.attempt_signoff(view, fb, ["L2A2", "B"])
        self.assertTrue(result.ok, result.reasons)
        self.assertIn("WA-T3", result.evidence_ids)

    def test_already_accepted_not_resigned(self):
        result = signoff_mod.attempt_signoff(self.view, self.fb, ["L2A1"])
        self.assertFalse(result.ok)
        self.assertIn("已", "；".join(result.reasons))


class WithdrawalTest(unittest.TestCase):
    def setUp(self):
        self.view = load()
        self.fb = fb_of(self.view)

    def test_withdraw_r1_degrades_h1(self):
        """共享检查被撤回，找出真正依赖它的签认。"""
        deps = signoff_mod.true_dependents(self.view, self.fb, "WA-R1")
        self.assertEqual([d[1] for d in deps], ["H1"])

    def test_withdraw_f2_does_not_touch_h1(self):
        deps = signoff_mod.true_dependents(self.view, self.fb, "WA-F2")
        self.assertEqual(deps, [])

    def test_withdraw_t2_degrades_h1_via_isolation(self):
        deps = signoff_mod.true_dependents(self.view, self.fb, "WA-T2")
        self.assertEqual([d[1] for d in deps], ["H1"])


class HandoverTest(unittest.TestCase):
    def test_existing_states(self):
        view = load()
        fb = fb_of(view)
        states = {s.segment: s for s in build_states(view, fb)}
        self.assertEqual(states["L2A1"].state, "已接管")
        self.assertEqual(states["L2A1"].open_items, [])  # WA-E1明确L2A1未施工
        self.assertEqual(states["L2A2"].state, "待交接")
        kinds = {i.kind for i in states["L2A2"].open_items}
        self.assertIn("局部重验", kinds)
        self.assertIn("不合格待复工", kinds)

    def test_post_handover_change_creates_suspend_item(self):
        """已接管区段受变更影响时产生停用待确认事项，不能改成从未通过。"""
        view = load([{"type": "record", "record": {
            "id": "WA-X1", "kind": "construction",
            "text": "九月一日J50改接。"}}])
        fb = fb_of(view)
        states = {s.segment: s for s in build_states(view, fb)}
        l1 = states["L2A1"]
        self.assertEqual(l1.state, "已接管")  # 历史签收不改写
        self.assertIn("停用待确认", l1.display_state)
        self.assertEqual(l1.open_items[0].kind, "停用待确认")
        self.assertEqual(l1.open_items[0].evidence[0].record_id, "WA-X1")


if __name__ == "__main__":
    unittest.main()
