"""移交状态与交接后未结事项。

已签收依据仍原样可查；已接管区段受变更影响时产生停用待确认事项，
不能改成从未通过。待交接区段受新施工影响时产生局部重验事项。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import model as M
from .evaluate import evaluate_segment
from .parse import fmt_date


@dataclass
class OpenItem:
    kind: str          # 停用待确认 / 局部重验 / 参数待确认 / 不合格待复工 / 缺项待补
    summary: str
    evidence: list = field(default_factory=list)


@dataclass
class SegmentState:
    segment: str
    state: str                 # 已接管 / 已签认 / 待交接
    handover_id: str | None
    accepted_date: tuple | None
    eval: object               # SegmentEval
    open_items: list = field(default_factory=list)

    @property
    def display_state(self):
        if self.state in ("已接管", "已签认") and any(
                i.kind == "停用待确认" for i in self.open_items):
            return f"{self.state}（有停用待确认事项）"
        return self.state


def _handover_date(fb, source_id):
    for h in fb.handovers:
        if h.record_id == source_id:
            return h.date
    return None


def accepted_map(view, fb):
    """段 → (办理编号, 签收日期)。已接管在前，已签认在后。"""
    out = {}
    for e in view.existing:
        if e.get("state") == "accepted":
            date = _handover_date(fb, e.get("source")) if e.get("source") else None
            for s in e.get("segments", []):
                out[s] = (e["id"], date, "已接管")
    for s in view.signoffs:
        for seg in s.get("segments", []):
            out.setdefault(seg, (s["id"], None, "已签认"))
    return out


def _affects(construction, seg):
    if seg in construction.spared_segments:
        return False
    if seg in construction.affected_segments:
        return True
    return any(s == seg for (s, _j) in construction.affected_interfaces)


def _pending_items(seval):
    """待交接段的未结事项：新施工局部重验、参数待确认、不合格待复工等。"""
    items = []
    for c in seval.criteria:
        if c.status == M.OK:
            continue
        if c.kind == "pressure":
            rework = any("施工" in m or "失效" in m or "接入" in m
                         for m in c.missing)
            kind = "局部重验" if rework else "缺项待补"
        elif c.kind == "flushing":
            kind = {M.INCOMPLETE: "参数待确认",
                    M.FAILED: "不合格待复工"}.get(c.status, "缺项待补")
        else:
            kind = "缺项待补"
        items.append(OpenItem(kind, "；".join(c.missing)))
    return items


def build_states(view, fb):
    """逐段移交状态。已接管段只增列停用待确认事项，不改写历史签收。"""
    accepted = accepted_map(view, fb)
    states = []
    for seg in view.segments:
        seval = evaluate_segment(seg, view, fb)
        items = []
        if seg in accepted:
            hid, date, state = accepted[seg]
            for c in fb.construction:  # 交接后施工 → 停用待确认
                if date and c.date and c.date > date and _affects(c, seg):
                    items.append(OpenItem(
                        "停用待确认",
                        f"{c.record_id}{c.verb}（{fmt_date(c.date)}）影响本段，"
                        "需监理确认后方可继续供水；原签收依据仍原样可查",
                        [M.Evidence(c.record_id, c.text)]))
        else:
            hid, date, state = None, None, "待交接"
            items = _pending_items(seval)
        states.append(SegmentState(seg, state, hid, date, seval, items))
    return states
