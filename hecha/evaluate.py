"""核对：把事实按验收约定逐段判定。

要点：
- 证据按元素（管段、接口）分别核对，切断重接只让受影响的局部证据失效；
- 不把不同日期且中间改过接头的报告拼成一次完整试验；
- 参数不全与明确不合格给不同处理；未记数值不从“冲洗完成”猜；
- 转述不是独立观察，必须与证据核对。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import model as M
from .parse import ParseContext, parse_record, fmt_date, fmt_num

_EVIDENCE_KINDS = {"pressure-test", "flushing", "construction"}
# 试压为合取核对：明确不合格最重，不能被其他段的参数不全掩盖
_PRECEDENCE = {M.OK: 0, M.NO_EVIDENCE: 1, M.INCOMPLETE: 2, M.FAILED: 3}


class FactBase:
    def __init__(self):
        self.pressure = []
        self.flush = []
        self.construction = []
        self.renumber = []
        self.isolation = []
        self.corrections = []
        self.confirmations = []           # 已接受的确认
        self.rejected_confirmations = []  # 被拒绝的确认（含理由）
        self.claims = []
        self.handovers = []
        self.warnings = []
        self._raw_confirmations = []


def build_factbase(view):
    """记录 → 事实。先登记（改号），再解析其余；更正落位；确认校验。"""
    ctx = ParseContext(view.segments, view.joints)
    fb = FactBase()
    records = view.active_records()
    for rec in records:  # 改号登记先解析，旧号才能映射到现段号
        if rec.kind == "drawing":
            _add(fb, parse_record(rec, ctx))
    for rec in records:
        if rec.kind == "drawing":
            continue
        if rec.kind in _EVIDENCE_KINDS and rec.stage not in (
                M.STAGE_EXECUTE, M.STAGE_COMPLETE):
            fb.warnings.append(f"{rec.id}：{rec.stage}不作执行证据，仅保留原文")
            continue
        _add(fb, parse_record(rec, ctx))
    for entry in view.confirmations:  # 办理日志中的结构化确认
        fb._raw_confirmations.append(M.ConfirmationFact(
            entry["id"], entry["target"], entry["param"],
            float(entry["value"]), entry.get("unit", ""),
            entry.get("source"), frozenset(entry.get("scope", [])),
            entry.get("text") or
            f"确认{entry['target']}{entry['param']}"
            f"{fmt_num(entry['value'])}{entry.get('unit', '')}"))
    for c in fb.corrections:  # 更正落位到被更正记录
        for f in fb.flush:
            if f.record_id == c.target:
                f.interrupted = True
                f.longest_continuous_min = c.longest_continuous_min
                f.correction_id = c.record_id
    for conf in fb._raw_confirmations:  # 确认校验：通过才作证据
        check_confirmation(conf, fb)
    fb.warnings.extend(ctx.warnings)
    return fb


def _add(fb, facts):
    for f in facts:
        if isinstance(f, M.PressureFact):
            fb.pressure.append(f)
        elif isinstance(f, M.FlushFact):
            fb.flush.append(f)
        elif isinstance(f, M.ConstructionFact):
            fb.construction.append(f)
        elif isinstance(f, M.RenumberFact):
            fb.renumber.append(f)
        elif isinstance(f, M.IsolationFact):
            fb.isolation.append(f)
        elif isinstance(f, M.CorrectionFact):
            fb.corrections.append(f)
        elif isinstance(f, M.ConfirmationFact):
            fb._raw_confirmations.append(f)
        elif isinstance(f, M.ClaimFact):
            fb.claims.append(f)
        elif isinstance(f, M.HandoverFact):
            fb.handovers.append(f)


# ---------------------------------------------------------------- 解释确认

def check_confirmation(conf, fb):
    """校验一条确认。监理可确认解释或补有来源的实测值，
    不能确认未观察时段，不能覆盖已有数值或明确不合格的记录。"""
    reasons = []
    target_flush = next((f for f in fb.flush if f.record_id == conf.target), None)
    target_pressure = next(
        (p for p in fb.pressure if p.record_id == conf.target), None)
    if conf.param in (M.PARAM_FLUSH_DURATION, M.PARAM_FLOW):
        if target_flush is None:
            reasons.append(f"确认对象{conf.target}不是冲洗记录")
        else:
            if conf.param == M.PARAM_FLUSH_DURATION:
                if target_flush.interrupted:
                    reasons.append(
                        f"{conf.target}已被更正为中断（明确不合格），"
                        "不能确认，需重新冲洗")
                elif target_flush.duration_min is not None:
                    reasons.append(
                        f"{conf.target}已记录连续"
                        f"{fmt_num(target_flush.duration_min)}分钟，不能确认覆盖")
            if conf.param == M.PARAM_FLOW and target_flush.flow_lps is not None:
                reasons.append(f"{conf.target}已记录流量，不能确认覆盖")
            if conf.scope and conf.scope - {target_flush.segment}:
                reasons.append("确认适用范围超出原记录范围")
    elif conf.param in (M.PARAM_PRESSURE, M.PARAM_TEST_DURATION):
        if target_pressure is None:
            reasons.append(f"确认对象{conf.target}不是试压记录")
        elif conf.param == M.PARAM_PRESSURE and target_pressure.pressure_mpa is not None:
            reasons.append(f"{conf.target}已记录压力，不能确认覆盖")
        elif conf.param == M.PARAM_TEST_DURATION and target_pressure.duration_min is not None:
            reasons.append(f"{conf.target}已记录试压时长，不能确认覆盖")
    else:
        reasons.append(f"未知参数“{conf.param}”")
    if not conf.source:
        reasons.append("未记参数不能凭确认补值，须注明来源（不能确认未观察时段）")
    if any(c.target == conf.target and c.param == conf.param
           for c in fb.confirmations):
        reasons.append(f"{conf.target}的{conf.param}已有确认，不能重复确认")
    if reasons:
        conf.rejected = tuple(reasons)
        fb.rejected_confirmations.append(conf)
    else:
        fb.confirmations.append(conf)
    return conf


# ---------------------------------------------------------------- 元素与失效

def _elements(view, seg):
    """段的核对元素：管段本体 + 每个接头处的本侧接口。"""
    els = [("管段", seg, None)]
    for jid, joint in view.joints.items():
        if seg in joint.joins:
            els.append(("接口", seg, jid))
    return els


def _el_text(el):
    return f"管段{el[1]}" if el[0] == "管段" else f"接口{el[1]}—{el[2]}"


def _last_construction(fb, view, seg):
    """每个元素最后一次相关施工（切断重接/接入/新增施工）。"""
    last = {}
    for c in fb.construction:
        if seg in c.spared_segments:
            continue
        if c.date is None:
            continue  # 无日期施工不参与失效排序
        if seg in c.affected_segments:
            keys = _elements(view, seg)
        else:
            keys = [("接口", s, j) for (s, j) in c.affected_interfaces
                    if s == seg]
        for key in keys:
            cur = last.get(key)
            if cur is None or (c.date, c.order) > (cur[0], cur[1]):
                last[key] = (c.date, c.order, c)
    return last


def _coverage(fb, el, last, params):
    """元素的有效覆盖、已失效覆盖与参数问题覆盖。

    不同日期且中间改过接头的报告不能拼成一次完整试验：
    施工前的覆盖对受影响元素直接失效，不参与拼接。
    """
    valid, invalid, problem = [], [], []
    for p in fb.pressure:
        if el[0] == "管段":
            covered = el[1] in p.segments
        else:
            covered = (el[1], el[2]) in p.interfaces
        if not covered or el[1] in p.excluded:
            continue
        lc = last.get(el)
        if lc and p.date and (p.date, p.order) <= (lc[0], lc[1]):
            invalid.append((p, lc[2]))
            continue
        if p.pressure_mpa is None or p.duration_min is None or p.no_leak is None:
            problem.append((p, M.INCOMPLETE, "压力、时长或结果未记全"))
            continue
        if p.no_leak is False:
            problem.append((p, M.FAILED, "记录有渗漏"))
            continue
        if (p.pressure_mpa < params["pressure_mpa"]
                or p.duration_min < params["duration_min"]):
            problem.append((p, M.FAILED, "试压参数低于约定"))
            continue
        valid.append(p)
    return valid, invalid, problem


# ---------------------------------------------------------------- 逐项核对

@dataclass
class CriterionEval:
    criterion_id: str
    kind: str
    status: str
    evidence: list = field(default_factory=list)  # Evidence
    missing: list = field(default_factory=list)   # 具体缺项
    handling: str = ""                            # 处理方式


@dataclass
class SegmentEval:
    segment: str
    criteria: list

    @property
    def ok(self):
        return all(c.status == M.OK for c in self.criteria)

    @property
    def verdict(self):
        if self.ok:
            return "可签认"
        sts = {c.status for c in self.criteria}
        if M.FAILED in sts:
            return "不可签认（含明确不合格项，需重新执行）"
        if M.NO_EVIDENCE in sts:
            return "不可签认（缺证据）"
        return "暂缓（参数不全，可办理解释确认）"


def eval_pressure(seg, view, fb):
    crit = view.criterion("pressure")
    if crit is None:
        return CriterionEval("?", "pressure", M.NO_EVIDENCE,
                             missing=["资料包无试压约定"])
    last = _last_construction(fb, view, seg)
    evidence, missing = [], []
    worst = M.OK
    for el in _elements(view, seg):
        valid, invalid, problem = _coverage(fb, el, last, crit.params)
        if valid:
            for p in valid:
                evidence.append(M.Evidence(p.record_id, p.text,
                                           f"覆盖{_el_text(el)}"))
            continue
        parts = []
        for p, c in invalid:
            parts.append(f"{p.record_id}（{fmt_date(p.date)}）证据因"
                         f"{c.record_id}{c.verb}（{fmt_date(c.date)}）失效")
        lc = last.get(el)
        if lc is not None and not invalid:
            parts.append(f"{lc[2].record_id}{lc[2].verb}"
                         f"（{fmt_date(lc[0])}）后无试压证据")
        for p, status, why in problem:
            parts.append(f"{p.record_id}{why}")
            if _PRECEDENCE[status] > _PRECEDENCE[worst]:
                worst = status
        for p in fb.pressure:  # 排除范围不丢：摘要不得删掉“除外”
            if el[1] in p.excluded:
                parts.append(f"{p.record_id}明确不含本段（“{p.text}”）")
        if not parts:
            parts.append("无试压证据")
        missing.append(f"{_el_text(el)}：" + "；".join(parts))
    if missing:
        status = worst if worst != M.OK else M.NO_EVIDENCE
    else:
        status = M.OK
    handling = {M.OK: "", M.NO_EVIDENCE: "需补充试压",
                M.INCOMPLETE: "可办理解释确认，补有来源的实测值",
                M.FAILED: "需重新试压，不能确认补救"}[status]
    return CriterionEval(crit.id, "pressure", status, evidence,
                         missing, handling)


def _flush_event_status(f, seg, fb, params):
    """一条冲洗记录对约定的核对。返回 (结论, 依据, 缺项)。"""
    evidence = [M.Evidence(f.record_id, f.text, "冲洗记录")]
    problems = []
    if f.flow_lps is None:
        problems.append((M.INCOMPLETE, "流量未记，不能从“冲洗完成”猜数值"))
    elif f.flow_lps < params["flow_lps"]:
        problems.append((M.FAILED,
                         f"流量每秒{fmt_num(f.flow_lps)}升，"
                         f"不足每秒{fmt_num(params['flow_lps'])}升"))
    duration = None
    if f.interrupted:
        corr = next((c for c in fb.corrections
                     if c.record_id == f.correction_id), None)
        if corr:
            evidence.append(M.Evidence(corr.record_id, corr.text, "更正"))
        problems.append((M.FAILED,
                         f"停流后连续冲洗最长"
                         f"{fmt_num(f.longest_continuous_min)}分钟，"
                         f"不足{fmt_num(params['continuous_min'])}分钟"
                         "（累计时长不等于连续时长）"))
    elif f.duration_min is not None:
        duration = f.duration_min
    else:
        confs = [c for c in fb.confirmations
                 if c.target == f.record_id
                 and c.param == M.PARAM_FLUSH_DURATION
                 and (not c.scope or seg in c.scope)]
        if confs:
            duration = confs[-1].value
            evidence.append(M.Evidence(
                confs[-1].record_id, confs[-1].text,
                f"监理确认（来源：{confs[-1].source}）"))
        else:
            problems.append((M.INCOMPLETE,
                             "持续时间未记，不能从“冲洗完成”猜数值；"
                             "可由监理凭来源办理解释确认"))
    if duration is not None and duration < params["continuous_min"]:
        problems.append((M.FAILED,
                         f"连续冲洗{fmt_num(duration)}分钟，"
                         f"不足{fmt_num(params['continuous_min'])}分钟"))
    if not problems:
        return M.OK, evidence, []
    worst = M.FAILED if any(s == M.FAILED for s, _ in problems) else M.INCOMPLETE
    return worst, evidence, [text for _, text in problems]


def eval_flushing(seg, view, fb):
    crit = view.criterion("flushing")
    if crit is None:
        return CriterionEval("?", "flushing", M.NO_EVIDENCE,
                             missing=["资料包无冲洗约定"])
    events = [f for f in fb.flush if f.segment == seg]
    if not events:
        return CriterionEval(crit.id, "flushing", M.NO_EVIDENCE,
                             missing=[f"管段{seg}：无冲洗记录"],
                             handling="需补充冲洗并记录流量与连续时长")
    results = [_flush_event_status(f, seg, fb, crit.params) for f in events]
    for status, evidence, _ in results:
        if status == M.OK:
            return CriterionEval(crit.id, "flushing", M.OK, evidence)
    # 参数不全可由确认补救，明确不合格只能重新执行：两者给不同处理
    if any(status == M.INCOMPLETE for status, _, _ in results):
        status = M.INCOMPLETE
        handling = "可办理解释确认，补有来源的实测值"
    else:
        status = M.FAILED
        handling = "需重新冲洗，不能确认补救"
    evidence, missing = [], []
    for _, ev, ms in results:
        evidence += ev
        missing += ms
    return CriterionEval(crit.id, "flushing", status, evidence,
                         missing, handling)


def eval_isolation(seg, view, fb, batch=()):
    """局部接管有有效隔离；共用接头或上游隔离不满足时不能留给运维猜。"""
    crit = view.criterion("isolation")
    if crit is None:
        return CriterionEval("?", "isolation", M.NO_EVIDENCE,
                             missing=["资料包无隔离约定"])
    verified = view.verified_segments()
    batch = set(batch) | {seg}
    evidence, missing = [], []
    for jid, joint in view.joints.items():
        if seg not in joint.joins:
            continue
        for neighbor in joint.joins:
            if neighbor == seg or neighbor in batch:
                continue
            if neighbor in verified:
                evidence.append(M.Evidence(
                    "", f"相邻段{neighbor}已验收", "相邻边界"))
                continue
            iso = [i for i in fb.isolation if i.joint == jid]
            if iso:
                for i in iso:
                    evidence.append(M.Evidence(
                        i.record_id, i.text,
                        f"接头{jid}隔离阀{i.valve}有效"))
            else:
                missing.append(
                    f"共用接头{jid}：相邻段{neighbor}未验收，"
                    "且无有效隔离证据，不能留给运维猜")
    status = M.OK if not missing else M.NO_EVIDENCE
    handling = "" if not missing else "需有效隔离，或与相邻段同批验收"
    return CriterionEval(crit.id, "isolation", status, evidence,
                         missing, handling)


def evaluate_segment(seg, view, fb, batch=()):
    return SegmentEval(seg, [
        eval_pressure(seg, view, fb),
        eval_flushing(seg, view, fb),
        eval_isolation(seg, view, fb, batch),
    ])


# ---------------------------------------------------------------- 转述核对

@dataclass
class ClaimCheck:
    claim: M.ClaimFact
    problems: list

    @property
    def ok(self):
        return not self.problems


def check_claims(view, fb):
    """转述/主张与证据核对。编号顺序不是效力优先级；
    不能因来源有引用便把转述当独立观察。"""
    out = []
    for claim in fb.claims:
        problems = []
        if any(k in claim.summary for k in ("无渗漏", "全线", "合格")):
            for seg in view.segments:
                ev = eval_pressure(seg, view, fb)
                if ev.status != M.OK:
                    problems.append(
                        f"段{seg}试压{ev.status}（{'；'.join(ev.missing)}）")
        out.append(ClaimCheck(claim, problems))
    return out
