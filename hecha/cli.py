"""命令行入口：录入、解释确认、局部签认、移交查询、撤回。

全部工程内容为合成演练，本工具只核对资料，不自动放行真实工程。
"""
from __future__ import annotations

import argparse
import sys

from . import model as M
from . import report
from . import signoff as signoff_mod
from . import store
from .evaluate import build_factbase, check_claims, check_confirmation
from .handover import build_states
from .parse import classify_stage, fmt_num

DEFAULT_CASE = "data/case.json"
DEFAULT_JOURNAL = "data/journal.json"


def _load(args):
    view = store.load_view(args.case, args.journal)
    fb = build_factbase(view)
    return view, fb


def _print_states(view, fb):
    for state in build_states(view, fb):
        kinds = "、".join(i.kind for i in state.open_items) or "无"
        print(f"  段{state.segment}：{state.display_state}；未结事项：{kinds}")


def cmd_ingest(args):
    """录入：校验资料并追加新记录（运维接管后登记冲洗、改接、复测同此）。"""
    view, fb = _load(args)
    if view.problems:
        print("资料校验问题：")
        for p in view.problems:
            print(f"  - {p}")
        return 1
    if args.编号:
        if args.编号 in view.record_ids():
            print(f"记录编号{args.编号}已存在，录入只追加不覆盖")
            return 1
        if args.种类 not in store.RECORD_KINDS:
            print(f"未知资料种类{args.种类}，可选：{'、'.join(sorted(store.RECORD_KINDS))}")
            return 1
        if not args.原文.strip():
            print("原文不能为空")
            return 1
        store.append_entry(args.journal, {
            "type": "record",
            "record": {"id": args.编号, "kind": args.种类, "text": args.原文}})
        print(f"已追加记录{args.编号}（{args.种类}），原文保留，可反复核对")
        view, fb = _load(args)
        if view.problems:
            print("资料校验问题：")
            for p in view.problems:
                print(f"  - {p}")
            return 1
    print(report.render_factbase(fb))
    if fb.warnings:
        print("解析警示：")
        for w in fb.warnings:
            print(f"  - {w}")
    print("当前各段状态：")
    _print_states(view, fb)
    return 0


def cmd_confirm(args):
    """解释确认：监理补有来源的实测值；未观察时段不能确认。"""
    view, fb = _load(args)
    taken = {c.record_id for c in fb.confirmations} | view.record_ids()
    cid = args.编号 or store.next_id("CF", taken | {c.record_id for c in fb.rejected_confirmations})
    entry = {"type": "confirmation", "id": cid, "target": args.针对,
             "param": args.参数, "value": args.值, "unit": args.单位,
             "source": args.来源, "scope": args.适用 or [],
             "text": args.备注 or ""}
    conf = M.ConfirmationFact(cid, args.针对, args.参数, float(args.值),
                              args.单位, args.来源, frozenset(args.适用 or []),
                              args.备注 or
                              f"确认{args.针对}{args.参数}{fmt_num(args.值)}{args.单位}")
    check_confirmation(conf, fb)
    if conf.rejected:
        print(f"确认被拒绝（{cid}）：")
        for r in conf.rejected:
            print(f"  - {r}")
        return 1
    store.append_entry(args.journal, entry)
    print(f"确认已登记（{cid}）：{args.针对}{args.参数}"
          f"={fmt_num(args.值)}{args.单位}（来源：{args.来源}）")
    view, fb = _load(args)
    print("当前各段状态：")
    _print_states(view, fb)
    return 0


def cmd_signoff(args):
    """局部签认：按具备独立边界的区段签认，可同批多段。"""
    view, fb = _load(args)
    result = signoff_mod.attempt_signoff(view, fb, args.段)
    if not result.ok:
        print(report.render_signoff(result))
        return 1
    sid = store.next_id("S", {s["id"] for s in view.signoffs})
    store.append_entry(args.journal, signoff_mod.make_signoff_entry(sid, result))
    print(report.render_signoff(result, sid))
    return 0


def cmd_query(args):
    """移交查询：每段原句证据、缺项、相邻段不能搭便车的原因、未结事项。"""
    view, fb = _load(args)
    states = build_states(view, fb)
    checks = check_claims(view, fb)

    def dependents_of(rid):
        return signoff_mod.true_dependents(view, fb, rid)

    print(report.render_query(view, fb, states, checks, dependents_of,
                              only=set(args.段 or [])))
    return 0


def cmd_withdraw(args):
    """撤回检查：找出真正依赖它的签认。原文保留，只不作证据。"""
    view, fb = _load(args)
    if args.记录 not in view.record_ids():
        print(f"记录{args.记录}不存在")
        return 1
    if args.记录 in view.withdrawals:
        print(f"记录{args.记录}已撤回")
        return 1
    deps = signoff_mod.true_dependents(view, fb, args.记录)
    store.append_entry(args.journal, {
        "type": "withdrawal", "record": args.记录,
        "reason": args.理由 or ""})
    print(f"已撤回{args.记录}（原文保留，不再作证据）")
    if deps:
        print("真正依赖它的签认：")
        for seg, hid, degraded in deps:
            print(f"  - {hid}（段{seg}，退化：{'、'.join(degraded)}）")
    else:
        print("无签认真正依赖该记录")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="hecha",
        description="饮水工程资料中文语义核对（合成演练，不自动放行真实工程）")
    parser.add_argument("--case", default=DEFAULT_CASE, help="资料包路径")
    parser.add_argument("--journal", default=DEFAULT_JOURNAL, help="办理日志路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("录入", help="校验资料并追加新记录")
    p.add_argument("--编号")
    p.add_argument("--种类")
    p.add_argument("--原文", default="")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("确认", help="监理解释确认（须有来源）")
    p.add_argument("--编号")
    p.add_argument("--针对", required=True, help="被确认的记录编号")
    p.add_argument("--参数", required=True,
                   choices=[M.PARAM_FLUSH_DURATION, M.PARAM_FLOW,
                            M.PARAM_PRESSURE, M.PARAM_TEST_DURATION])
    p.add_argument("--值", required=True, type=float)
    p.add_argument("--单位", default="分钟")
    p.add_argument("--来源", default=None)
    p.add_argument("--适用", nargs="*", default=None, help="适用段号")
    p.add_argument("--备注", default="")
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser("签认", help="局部签认（可同批多段）")
    p.add_argument("--段", nargs="+", required=True)
    p.set_defaults(func=cmd_signoff)

    p = sub.add_parser("查询", help="移交查询")
    p.add_argument("--段", nargs="*", default=None)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("撤回", help="撤回检查并找出真正依赖它的签认")
    p.add_argument("--记录", required=True)
    p.add_argument("--理由", default="")
    p.set_defaults(func=cmd_withdraw)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
