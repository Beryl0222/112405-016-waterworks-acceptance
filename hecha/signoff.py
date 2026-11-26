"""局部签认与检查撤回的影响分析。

- 可按具备独立边界的区段签认并局部移交；
- 共用接头或上游隔离不满足时，不能签认后留给运维猜；
- 多个移交段共享必要检查，检查被撤回应找出真正依赖它的签认。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import model as M
from .evaluate import build_factbase, evaluate_segment


@dataclass
class SignoffResult:
    ok: bool
    segments: tuple
    evals: dict = field(default_factory=dict)
    evidence_ids: list = field(default_factory=list)
    reasons: list = field(default_factory=list)


def attempt_signoff(view, fb, segments):
    """尝试对一批区段局部签认。同批段的共用接头视为批内边界。"""
    batch = set(segments)
    reasons, evals = [], {}
    verified = view.verified_segments()
    for seg in segments:
        if seg not in view.segments:
            reasons.append(f"段{seg}未登记")
            continue
        if seg in verified:
            reasons.append(f"段{seg}已接管或已签认，无需重复签认")
            continue
        seval = evaluate_segment(seg, view, fb, batch)
        evals[seg] = seval
        if not seval.ok:
            reasons.append(f"段{seg}{seval.verdict}")
    ok = not reasons and bool(segments)
    evidence_ids = sorted({
        e.record_id
        for seval in evals.values()
        for c in seval.criteria
        for e in c.evidence if e.record_id})
    return SignoffResult(ok, tuple(segments), evals, evidence_ids, reasons)


def make_signoff_entry(entry_id, result):
    return {"type": "signoff", "id": entry_id,
            "segments": list(result.segments),
            "evidence": result.evidence_ids}


def true_dependents(view, fb, record_id):
    """撤回某记录后，结论由满足退化的已接管/已签认段。

    返回 [(段, 办理编号, 退化的约定)]，即真正依赖该记录的签认。
    """
    from .handover import accepted_map

    deps = []
    reduced = view.without(record_id)
    fb2 = build_factbase(reduced)
    for seg, (hid, _date, _state) in sorted(accepted_map(view, fb).items()):
        before = evaluate_segment(seg, view, fb)
        after = evaluate_segment(seg, reduced, fb2)
        if before.ok and not after.ok:
            degraded = [f"{c.criterion_id}{c.status}"
                        for c in after.criteria if c.status != M.OK]
            deps.append((seg, hid, degraded))
    return deps
