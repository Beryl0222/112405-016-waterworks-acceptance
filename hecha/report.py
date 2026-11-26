"""中文查询输出：每段的原句证据、具体缺项、相邻段为何不能搭便车、
交接后未结事项。"""
from __future__ import annotations

from . import model as M
from .handover import accepted_map
from .parse import fmt_date

_DISCLAIMER = "（全部工程内容为合成演练，本输出不构成真实工程放行依据）"


def _quote(evidence):
    if evidence.record_id:
        note = f"（{evidence.note}）" if evidence.note else ""
        return f"    依据 {evidence.record_id}：「{evidence.sentence}」{note}"
    return f"    {evidence.sentence}"


def _old_ids_text(seg, fb):
    olds = [f"{r.old_id}（{fmt_date(r.date)}改号，原范围{r.old_from}—{r.old_to}）"
            for r in fb.renumber if seg in r.new_ids]
    return "；旧号：" + "、".join(olds) if olds else ""


def adjacency_lines(seg, view, fb):
    """相邻段为何不能搭便车：共用接头分别核对、确认适用范围、
    签收排除与报告排除范围。"""
    accepted = accepted_map(view, fb)
    lines = []
    for jid, joint in sorted(view.joints.items()):
        if seg not in joint.joins:
            continue
        for neighbor in joint.joins:
            if neighbor == seg:
                continue
            nstate = accepted.get(neighbor, (None, None, "待交接"))[2]
            reasons = []
            for p in fb.pressure:  # 原句明确未测本侧接口（按接头归位）
                if jid not in p.text:
                    continue
                for _token, segs in p.not_covered:
                    if seg in segs:
                        reasons.append(
                            f"{p.record_id}原句明确未测本侧接口：「{p.text}」")
            for c in fb.confirmations:  # 确认适用范围
                if c.scope and neighbor in c.scope and seg not in c.scope:
                    reasons.append(f"{c.record_id}确认仅适用{neighbor}")
            for h in fb.handovers:  # 签收排除
                if seg in h.excluded and neighbor in h.segments:
                    reasons.append(f"{h.record_id}签收明确不含{seg}")
            for p in fb.pressure:  # 报告排除范围不丢
                if seg in p.excluded:
                    reasons.append(
                        f"{p.record_id}明确不含{seg}：「{p.text}」")
            if neighbor not in accepted:
                if seg in accepted:
                    reasons.append(f"{neighbor}未验收，其证据与状态不能推及本段")
                else:
                    reasons.append(f"{neighbor}同为待交接，不能互相推定")
            if not reasons:
                reasons.append(
                    f"共用接头{jid}为同一对象，两侧接口分别核对；"
                    f"{neighbor}（{nstate}）的证据不覆盖本段")
            lines.append((neighbor, nstate, jid, reasons))
    return lines


_KIND_CN = {"pressure": "试压", "flushing": "冲洗", "isolation": "隔离"}


def render_segment(state, view, fb):
    seg = state.segment
    s = view.segments[seg]
    lines = []
    head = f"段 {seg}（{s.from_stake}—{s.to_stake}）"
    head += _old_ids_text(seg, fb)
    hid = f"，{state.handover_id}" if state.handover_id else ""
    lines.append(f"{head}　状态：{state.display_state}{hid}")
    for c in state.eval.criteria:
        lines.append(f"  {c.criterion_id} {_KIND_CN.get(c.kind, c.kind)}：{c.status}")
        for ev in c.evidence:
            lines.append(_quote(ev))
        for m in c.missing:
            lines.append(f"    缺项：{m}")
        if c.handling:
            lines.append(f"    处理：{c.handling}")
    adj = adjacency_lines(seg, view, fb)
    if adj:
        lines.append("  相邻段为何不能搭便车：")
        for neighbor, nstate, jid, reasons in adj:
            lines.append(f"    相对 {neighbor}（{nstate}，共用{jid}）：")
            for r in reasons:
                lines.append(f"      - {r}")
    if state.open_items:
        lines.append("  未结事项：")
        for item in state.open_items:
            lines.append(f"    [{item.kind}] {item.summary}")
            for ev in item.evidence:
                lines.append(_quote(ev))
    else:
        lines.append("  未结事项：无")
    return "\n".join(lines)


def render_claims(checks):
    lines = ["转述/主张核对（转述不是独立观察）："]
    if not checks:
        lines.append("  无")
    for chk in checks:
        claim = chk.claim
        if chk.ok:
            lines.append(f"  {claim.record_id}：「{claim.text}」→ 与证据一致")
        else:
            lines.append(f"  {claim.record_id}：「{claim.text}」→ 主张不成立，不能作为依据")
            for p in chk.problems:
                lines.append(f"    - {p}")
    return "\n".join(lines)


def render_withdrawals(view, fb, dependents_of):
    lines = ["被撤回检查的影响："]
    if not view.withdrawals:
        lines.append("  无")
    for rid in sorted(view.withdrawals):
        deps = dependents_of(rid)
        lines.append(f"  {rid} 已撤回（原文保留，不作证据）")
        if deps:
            for seg, hid, degraded in deps:
                lines.append(
                    f"    真正依赖它的签认：{hid}（段{seg}，退化：{'、'.join(degraded)}）")
        else:
            lines.append("    无签认真正依赖该记录")
    return "\n".join(lines)


def render_query(view, fb, states, checks, dependents_of, only=None):
    blocks = []
    if view.problems:
        blocks.append("资料校验问题：\n" + "\n".join(
            f"  - {p}" for p in view.problems))
    if fb.warnings:
        blocks.append("解析警示：\n" + "\n".join(
            f"  - {w}" for w in fb.warnings))
    for state in states:
        if only and state.segment not in only:
            continue
        blocks.append(render_segment(state, view, fb))
    blocks.append(render_claims(checks))
    blocks.append(render_withdrawals(view, fb, dependents_of))
    blocks.append(_DISCLAIMER)
    return "\n\n".join(blocks)


def render_factbase(fb):
    """录入后的事实清单，供监理确认解析无误。"""
    lines = ["解析出的事实："]
    for r in fb.renumber:
        lines.append(f"  改号 {r.record_id}：{r.old_id}"
                     f"（{r.old_from}—{r.old_to}）→ {'、'.join(r.new_ids)}")
    for p in fb.pressure:
        lines.append(
            f"  试压 {p.record_id}（{fmt_date(p.date)}）：段{'、'.join(sorted(p.segments)) or '未指明'}"
            f"；接口{'、'.join(f'{s}—{j}' for s, j in sorted(p.interfaces)) or '未点名'}"
            f"；排除{'、'.join(sorted(p.excluded)) or '无'}")
    for f in fb.flush:
        extra = "；已被更正中断" if f.interrupted else ""
        lines.append(f"  冲洗 {f.record_id}（{fmt_date(f.date)}）：段{f.segment}{extra}")
    for c in fb.construction:
        lines.append(
            f"  施工 {c.record_id}（{fmt_date(c.date)}）：{c.verb}；"
            f"影响接口{'、'.join(f'{s}—{j}' for s, j in sorted(c.affected_interfaces)) or '无'}"
            f"；明确未施工{'、'.join(sorted(c.spared_segments)) or '无'}")
    for i in fb.isolation:
        lines.append(f"  隔离 {i.record_id}：阀{i.valve}有效（接头{i.joint or '未定位'}）")
    for c in fb.confirmations:
        lines.append(f"  确认 {c.record_id}：{c.target}{c.param}"
                     f"={c.value}{c.unit}（来源：{c.source}）")
    for c in fb.rejected_confirmations:
        lines.append(f"  确认被拒 {c.record_id}：{'；'.join(c.rejected)}")
    for h in fb.handovers:
        lines.append(f"  签收 {h.record_id}（{fmt_date(h.date)}）："
                     f"{'、'.join(h.segments)}；不含{'、'.join(h.excluded) or '无'}")
    for c in fb.claims:
        lines.append(f"  转述 {c.record_id}：{c.summary}")
    return "\n".join(lines)


def render_signoff(result, entry_id=None):
    lines = []
    if result.ok:
        lines.append(f"局部签认成立（{entry_id}）：段{'、'.join(result.segments)}")
        lines.append(f"  依据记录：{'、'.join(result.evidence_ids)}")
        for seg, seval in result.evals.items():
            for c in seval.criteria:
                for ev in c.evidence:
                    lines.append(_quote(ev))
    else:
        lines.append(f"局部签认不成立：段{'、'.join(result.segments)}")
        for r in result.reasons:
            lines.append(f"  - {r}")
        for seg, seval in result.evals.items():
            for c in seval.criteria:
                for m in c.missing:
                    lines.append(f"  段{seg}缺项（{c.criterion_id}）：{m}")
                if c.handling:
                    lines.append(f"  段{seg}处理（{c.criterion_id}）：{c.handling}")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)
