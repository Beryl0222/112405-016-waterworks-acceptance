"""原文解析：把施工记录、试验报告等中文原句解析为可核对的事实。

解析只面向本演练资料的中文句式。每条事实都保留原句；
无法解析的内容记入警示，不静默丢弃。
"""
from __future__ import annotations

import re

from . import model as M

# ---------------------------------------------------------------- 中文数字

_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_number(text):
    """中文小整数或阿拉伯数字 → int；无法解析返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        if left and left not in _CN_DIGIT:
            return None
        if right and right not in _CN_DIGIT:
            return None
        tens = _CN_DIGIT.get(left, 1) if left else 1
        ones = _CN_DIGIT.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_DIGIT.get(text)


_NUM = r"(\d+(?:\.\d+)?|[一二两三四五六七八九十]+)"


def _num(text):
    value = cn_number(text)
    if value is not None:
        return float(value)
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def fmt_num(value):
    """整数不显示小数点，便于中文输出。"""
    if value is None:
        return "未记"
    value = float(value)
    return str(int(value)) if value.is_integer() else str(value)


# ---------------------------------------------------------------- 日期

_DATE_RE = re.compile(r"([一二三四五六七八九十]{1,3})月([一二三四五六七八九十]{1,3})日")


def parse_date(text):
    """“七月三十一日” → (7, 31)；无日期返回 None。事件发生与记录时间不同。"""
    m = _DATE_RE.search(text)
    if not m:
        return None
    month, day = cn_number(m.group(1)), cn_number(m.group(2))
    if month is None or day is None:
        return None
    return (month, day)


def fmt_date(date):
    if not date:
        return "日期未记"
    return f"{date[0]}月{date[1]}日"


# ---------------------------------------------------------------- 阶段

def classify_stage(kind, text):
    """计划、执行、完成、转述等严格分开；转述不是独立观察。"""
    if kind == "request" or "认为" in text or "应可" in text:
        return M.STAGE_HEARSAY
    if kind == "correction":
        return M.STAGE_CORRECTION
    if kind == "supervision":
        return M.STAGE_CONFIRM
    if kind == "drawing":
        return M.STAGE_REGISTER
    if kind == "handover":
        return M.STAGE_HANDLE
    if "计划" in text:
        return M.STAGE_PLAN
    if "完成" in text:
        return M.STAGE_COMPLETE
    return M.STAGE_EXECUTE


# ---------------------------------------------------------------- 解析上下文

class ParseContext:
    """段、接头登记与旧号映射。接头是独立对象，不以区间数值重叠替代。"""

    def __init__(self, segments, joints):
        self.segments = segments      # 段号 -> Segment
        self.joints = joints          # 接头号 -> Joint
        self.old_map = {}             # 旧号 -> [现段号]
        self.warnings = []
        self._segment_re = None

    def segment_re(self):
        if self._segment_re is None:
            ids = sorted(set(self.segments) | set(self.old_map),
                         key=len, reverse=True)
            alt = "|".join(re.escape(i) for i in ids)
            self._segment_re = re.compile(
                r"(?<![A-Za-z0-9])(" + alt + r")(?![A-Za-z0-9])")
        return self._segment_re

    def resolve(self, token):
        """段号或旧号 → 现段号列表。重编号不改变原试验范围。"""
        if token in self.segments:
            return [token]
        if token in self.old_map:
            return list(self.old_map[token])
        return []

    def mentioned_segments(self, text):
        found = []
        for m in self.segment_re().finditer(text):
            for seg in self.resolve(m.group(1)):
                if seg not in found:
                    found.append(seg)
        return found

    def joints_of(self, seg):
        return [j.id for j in self.joints.values() if seg in j.joins]

    def interfaces_of_joint(self, joint_id):
        joint = self.joints.get(joint_id)
        if not joint:
            return []
        return [(s, joint_id) for s in joint.joins]

    def joint_stake(self, joint_id):
        """接头所在桩号：所连接段的共同端点。"""
        joint = self.joints.get(joint_id)
        if not joint:
            return None
        endpoints = []
        for s in joint.joins:
            seg = self.segments.get(s)
            if seg:
                endpoints.extend([seg.from_stake, seg.to_stake])
        for stake in endpoints:
            if stake != joint_id and endpoints.count(stake) >= 2:
                return stake
        return None

    def joint_at_stake(self, stake):
        for jid in self.joints:
            if self.joint_stake(jid) == stake:
                return jid
        return None


def _segments_in_range(ctx, from_stake, to_stake):
    """桩号区间 → 段。先精确匹配，再取落在区间内的段。"""
    for seg in ctx.segments.values():
        if seg.from_stake == from_stake and seg.to_stake == to_stake:
            return [seg.id]

    def stake_num(stake):
        m = re.fullmatch(r"K(\d+)", stake or "")
        return int(m.group(1)) if m else None

    lo, hi = stake_num(from_stake), stake_num(to_stake)
    if lo is None or hi is None:
        return []
    return [s.id for s in ctx.segments.values()
            if stake_num(s.from_stake) is not None
            and stake_num(s.to_stake) is not None
            and lo <= stake_num(s.from_stake) and stake_num(s.to_stake) <= hi]


# ---------------------------------------------------------------- 验收约定

def parse_criterion(cid, text):
    """验收约定 → 结构化条件。数值与同时成立的工序分别保存。"""
    if "冲洗" in text:
        flow = re.search(r"每秒(?:不少于)?(\d+(?:\.\d+)?)升", text)
        cont = re.search(r"连续(?:不少于)?" + _NUM + r"分钟", text)
        if not flow or not cont:
            raise ValueError(f"约定{cid}无法解析：{text}")
        return M.Criterion(cid, text, "flushing",
                           {"flow_lps": float(flow.group(1)),
                            "continuous_min": _num(cont.group(1))})
    if "试压" in text:
        pressure = re.search(r"(\d+(?:\.\d+)?)\s*MPa", text)
        duration = re.search(r"试压\s*" + _NUM + r"\s*分钟", text)
        if not pressure or not duration:
            raise ValueError(f"约定{cid}无法解析：{text}")
        return M.Criterion(cid, text, "pressure",
                           {"pressure_mpa": float(pressure.group(1)),
                            "duration_min": _num(duration.group(1))})
    if "隔离" in text:
        return M.Criterion(cid, text, "isolation", {})
    raise ValueError(f"未知约定形式：{cid}：{text}")


# ---------------------------------------------------------------- 各类记录

def parse_record(rec, ctx):
    """按资料种类解析一条记录，返回事实列表。"""
    kind = rec.kind
    if kind == "drawing":
        return _parse_drawing(rec, ctx)
    if kind == "pressure-test":
        return _parse_pressure(rec, ctx)
    if kind == "construction":
        return _parse_construction(rec, ctx)
    if kind == "flushing":
        return _parse_flushing(rec, ctx)
    if kind == "correction":
        return _parse_correction(rec, ctx)
    if kind == "supervision":
        return _parse_confirmation(rec, ctx)
    if kind == "request":
        return _parse_claim(rec, ctx)
    if kind == "handover":
        return _parse_handover(rec, ctx)
    ctx.warnings.append(f"{rec.id}：未知资料种类“{kind}”，仅保留原文")
    return []


def _parse_drawing(rec, ctx):
    """图纸/登记：旧号区间与改号。改号未施工，不让旧证据失效。"""
    text = rec.text
    m = re.search(r"([A-Za-z0-9]+)从(K\d+)到(K\d+)", text)
    if not m:
        ctx.warnings.append(f"{rec.id}：登记未解析出桩号区间")
        return []
    old_id, from_stake, to_stake = m.group(1), m.group(2), m.group(3)
    new_ids = re.findall(r"([A-Za-z0-9]+)（K\d+-K\d+）", text)
    if "改号" in text and new_ids:
        ctx.old_map[old_id] = new_ids
        ctx._segment_re = None  # 旧号进入词汇表
        return [M.RenumberFact(rec.id, parse_date(text), old_id,
                               from_stake, to_stake, tuple(new_ids), text)]
    ctx.warnings.append(f"{rec.id}：登记未解析出改号关系")
    return []


def _parse_pressure(rec, ctx, order=0):
    """试压/复测。报告覆盖到接头不表示检查后来接上的另一侧接口：
    接口覆盖只计明确点名的接头；“只对X复测”计X在被点名接头处的本侧接口。"""
    text = rec.text
    date = parse_date(text)
    facts = []

    excluded = set()
    for m in re.finditer(r"不含(?:支管)?([A-Za-z0-9]+)", text):
        excluded.update(ctx.resolve(m.group(1)))

    not_covered = []
    for m in re.finditer(r"未测([A-Za-z0-9]+?)侧接口", text):
        not_covered.append((m.group(1), tuple(ctx.resolve(m.group(1)))))

    only = re.search(r"只对([A-Za-z0-9]+?)(?:复测|试压)", text)
    if only:
        segs = ctx.resolve(only.group(1))
    else:
        segs = []
        for m in re.finditer(r"(K\d+)\s*[-—]\s*(K\d+)", text):
            segs += _segments_in_range(ctx, m.group(1), m.group(2))
        if not segs:
            segs = ctx.mentioned_segments(text)
    segs = [s for s in dict.fromkeys(segs) if s not in excluded]

    interfaces = set()
    for m in re.finditer(r"(J\d+)两侧接口", text):
        interfaces.update(ctx.interfaces_of_joint(m.group(1)))
    for m in re.finditer(r"含(?:当时)?(J\d+)", text):
        for (s, j) in ctx.interfaces_of_joint(m.group(1)):
            if s in segs:
                interfaces.add((s, j))
    if only:
        for jid in re.findall(r"J\d+", text):
            for (s, j) in ctx.interfaces_of_joint(jid):
                if s in segs:
                    interfaces.add((s, j))
    interfaces = {(s, j) for (s, j) in interfaces if s not in excluded}

    pressure = re.search(r"(\d+(?:\.\d+)?)\s*MPa", text)
    duration = re.search(_NUM + r"\s*分钟", text)
    if "无渗漏" in text:
        no_leak = True
    elif "渗漏" in text:
        no_leak = False
    else:
        no_leak = None  # 结果未记：参数不全，不猜

    facts.append(M.PressureFact(
        rec.id, date, order, frozenset(segs), frozenset(interfaces),
        float(pressure.group(1)) if pressure else None,
        _num(duration.group(1)) if duration else None,
        no_leak, frozenset(excluded), tuple(not_covered), rec.stage, text))

    iso = re.search(r"(上游|下游)?隔离阀([A-Za-z0-9]+)有效", text)
    if iso:
        joint = None
        rm = re.search(r"(K\d+)\s*[-—]\s*(K\d+)", text)
        if rm:
            stake = rm.group(1) if iso.group(1) != "下游" else rm.group(2)
            joint = ctx.joint_at_stake(stake)
        facts.append(M.IsolationFact(rec.id, date, iso.group(2), joint, text))
    return facts


def _parse_construction(rec, ctx):
    """施工：切断重接、接入、新增施工只影响局部；明确“未施工”的段不受影响。"""
    text = rec.text
    date = parse_date(text)
    facts = []

    spared = set()
    for m in re.finditer(r"([A-Za-z0-9]+?)未施工", text):
        spared.update(ctx.resolve(m.group(1)))

    affected_if, affected_seg, verbs, joints_hit = set(), set(), [], set()
    for m in re.finditer(r"(J\d+)切断重接", text):
        verbs.append("切断重接")
        joints_hit.add(m.group(1))
        affected_if.update(ctx.interfaces_of_joint(m.group(1)))
    for m in re.finditer(r"(J\d+)改接", text):
        verbs.append("改接")
        joints_hit.add(m.group(1))
        affected_if.update(ctx.interfaces_of_joint(m.group(1)))
    for m in re.finditer(r"([A-Za-z0-9]+?)接入(J\d+)", text):
        verbs.append("接入")
        joints_hit.add(m.group(2))
        affected_seg.update(ctx.resolve(m.group(1)))
        affected_if.update(ctx.interfaces_of_joint(m.group(2)))
    if "新增施工" in text:
        verbs.append("新增施工")
        for jid in re.findall(r"J\d+", text):
            joints_hit.add(jid)
            affected_if.update(ctx.interfaces_of_joint(jid))

    affected_if = {(s, j) for (s, j) in affected_if if s not in spared}
    affected_seg -= spared
    if verbs:
        facts.append(M.ConstructionFact(
            rec.id, date, 0, "、".join(dict.fromkeys(verbs)),
            frozenset(joints_hit), frozenset(affected_if),
            frozenset(affected_seg), frozenset(spared), text))
    if "复测" in text or "试压" in text:
        facts.extend(_parse_pressure(rec, ctx, order=1))  # 施工后的复测排在其后
    if not facts:
        ctx.warnings.append(f"{rec.id}：施工记录未解析出受影响对象")
    return facts


def _parse_flushing(rec, ctx):
    """冲洗：未记时间或流量保持缺失，不从“充分冲洗/冲洗完成”猜数值。"""
    text = rec.text
    date = parse_date(text)
    segs = ctx.mentioned_segments(text)
    if not segs:
        ctx.warnings.append(f"{rec.id}：冲洗记录未指明段号")
        return []
    flow = re.search(r"每秒\s*(\d+(?:\.\d+)?)\s*升", text)
    unrecorded = "未记" in text
    duration = None
    m = re.search(_NUM + r"\s*分钟", text)
    if m and not unrecorded:
        duration = _num(m.group(1))
    return [M.FlushFact(rec.id, date, 0, seg,
                        float(flow.group(1)) if flow else None,
                        duration, unrecorded, "连续" in text,
                        stage=rec.stage, text=text)
            for seg in segs]


def _parse_correction(rec, ctx):
    """更正：如“第十二分钟停流两分钟，恢复后冲洗十三分钟”，
    累计25不是连续25，连续时长取各段实际最长。"""
    text = rec.text
    m = re.search(r"(WA-[A-Za-z0-9]+)", text)
    if not m:
        ctx.warnings.append(f"{rec.id}：更正未指明对象")
        return []
    at = re.search(r"第?" + _NUM + r"分钟停流" + _NUM + r"分钟", text)
    resume = re.search(r"恢复后冲洗" + _NUM + r"分钟", text)
    at_min = _num(at.group(1)) if at else None
    stop_min = _num(at.group(2)) if at else None
    resume_min = _num(resume.group(1)) if resume else None
    runs = [x for x in (at_min, resume_min) if x is not None]
    longest = max(runs) if runs else None
    return [M.CorrectionFact(rec.id, m.group(1), at_min, stop_min,
                             resume_min, longest, text)]


def _parse_confirmation(rec, ctx):
    """监理确认记录：对象、来源、数值、适用范围。"""
    text = rec.text
    m = re.search(r"(WA-[A-Za-z0-9]+)", text)
    if not m:
        ctx.warnings.append(f"{rec.id}：确认未指明对象")
        return []
    target = m.group(1)
    source = None
    sm = re.search(r"WA-[A-Za-z0-9]+\s*([一-鿿]+?)证实", text)
    if sm:
        source = sm.group(1)
    scope = []
    sc = re.search(r"仅适用([A-Za-z0-9]+)", text)
    if sc:
        scope = ctx.resolve(sc.group(1))

    facts = []
    vm = re.search(r"证实\s*(?:连续)?\s*" + _NUM + r"\s*分钟", text)
    if vm:
        facts.append(M.ConfirmationFact(
            rec.id, target, M.PARAM_FLUSH_DURATION, _num(vm.group(1)),
            "分钟", source, frozenset(scope), text))
    fm = re.search(r"证实\s*每秒\s*(\d+(?:\.\d+)?)\s*升", text)
    if fm:
        facts.append(M.ConfirmationFact(
            rec.id, target, M.PARAM_FLOW, float(fm.group(1)),
            "升/秒", source, frozenset(scope), text))
    if not facts:
        ctx.warnings.append(f"{rec.id}：确认未解析出参数与数值")
    return facts


def _parse_claim(rec, ctx):
    """转述/主张：保留原文与引用，但不作独立观察。"""
    text = rec.text
    parts = [m.group(1) for m in re.finditer(r"认为(.+?)[，。；]", text)]
    parts += ["应可" + m.group(1) for m in re.finditer(r"应可(.+?)[，。；]", text)]
    refs = tuple(re.findall(r"WA-[A-Za-z0-9]+", text))
    return [M.ClaimFact(rec.id, "；".join(parts) or text, refs, text)]


def _parse_handover(rec, ctx):
    """签收办理：段、依据、排除范围。"""
    text = rec.text
    segs = []
    m = re.search(r"签收([A-Za-z0-9]+)", text)
    if m:
        segs = ctx.resolve(m.group(1))
    basis = tuple(re.findall(r"WA-[A-Za-z0-9]+", text))
    excluded = []
    for m2 in re.finditer(r"不含([A-Za-z0-9]+)", text):
        excluded += ctx.resolve(m2.group(1))
    return [M.HandoverFact(rec.id, parse_date(text), tuple(segs),
                           tuple(excluded), basis, text)]
