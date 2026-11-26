"""数据模型：合成饮水工程资料核对的对象、事实与结论。

所有内容均为合成演练；模型只描述资料与核对结论，不构成真实工程放行依据。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---- 记录阶段：计划、执行、完成、转述等严格分开，互不替代 ----
STAGE_PLAN = "计划"
STAGE_EXECUTE = "执行"
STAGE_COMPLETE = "完成"
STAGE_HEARSAY = "转述"
STAGE_CONFIRM = "确认"
STAGE_CORRECTION = "更正"
STAGE_REGISTER = "登记"
STAGE_HANDLE = "办理"

# ---- 核对结论 ----
OK = "满足"
NO_EVIDENCE = "缺证据"
INCOMPLETE = "参数不全"
FAILED = "明确不合格"

# ---- 可确认的参数名 ----
PARAM_PRESSURE = "压力"
PARAM_TEST_DURATION = "试压时长"
PARAM_FLOW = "流量"
PARAM_FLUSH_DURATION = "连续时长"


@dataclass
class Record:
    """一条原文记录。text 永远保留原句；stage 由原文与种类判定。"""
    id: str
    kind: str
    text: str
    stage: str
    origin: str = "case"  # case=资料包原文；journal=办理中追加


@dataclass
class Segment:
    id: str
    from_stake: str
    to_stake: str


@dataclass
class Joint:
    """接头是独立对象：区间端点的同一接头只是一处对象。"""
    id: str
    joins: tuple


@dataclass
class Criterion:
    """验收约定：数值、同时成立的工序、排除范围分别保存。"""
    id: str
    text: str
    kind: str              # pressure / flushing / isolation
    params: dict


@dataclass
class Evidence:
    """一条依据：记录编号 + 原句 + 说明。查询时按原句展示。"""
    record_id: str
    sentence: str
    note: str = ""


@dataclass
class PressureFact:
    """一次试压/复测。覆盖范围按段与接口分别记录，排除范围不丢。"""
    record_id: str
    date: tuple | None
    order: int                       # 同一记录内的事件先后
    segments: frozenset              # 覆盖的段（现段号）
    interfaces: frozenset            # 覆盖的接口 (段号, 接头号)
    pressure_mpa: float | None
    duration_min: float | None
    no_leak: bool | None             # True=无渗漏 False=有渗漏 None=结果未记
    excluded: frozenset              # 明确排除的段（如“不含支管B”）
    not_covered: tuple               # 明确未覆盖说明（如“未测L2A2侧接口”）
    stage: str
    text: str


@dataclass
class FlushFact:
    """一次冲洗。未记时间或流量保持缺失，不从“冲洗完成”猜数值。"""
    record_id: str
    date: tuple | None
    order: int
    segment: str
    flow_lps: float | None
    duration_min: float | None
    duration_unrecorded: bool
    claims_continuous: bool
    interrupted: bool = False        # 被更正记录打断
    longest_continuous_min: float | None = None
    correction_id: str | None = None
    stage: str = ""
    text: str = ""


@dataclass
class ConstructionFact:
    """一次施工。切断重接、接入、新增施工只让受影响的局部证据失效。"""
    record_id: str
    date: tuple | None
    order: int
    verb: str
    joints: frozenset
    affected_interfaces: frozenset   # (段号, 接头号)
    affected_segments: frozenset     # 段本体受施工影响（如新建接入）
    spared_segments: frozenset       # 明确“未施工”的段
    text: str


@dataclass
class RenumberFact:
    """改号登记：重编号不改变原试验范围。"""
    record_id: str
    date: tuple | None
    old_id: str
    old_from: str
    old_to: str
    new_ids: tuple
    text: str


@dataclass
class CorrectionFact:
    """更正记录：落到被更正的记录上，如停流使“连续25”不成立。"""
    record_id: str
    target: str
    at_minute: float | None
    stop_min: float | None
    resume_min: float | None
    longest_continuous_min: float | None
    text: str


@dataclass
class IsolationFact:
    """隔离有效的记录，定位到边界接头。"""
    record_id: str
    date: tuple | None
    valve: str
    joint: str | None
    text: str


@dataclass
class ConfirmationFact:
    """监理解释确认：只能补有来源的实测值，不能确认未观察时段，
    也不能覆盖已有数值或明确不合格的记录。"""
    record_id: str
    target: str
    param: str
    value: float
    unit: str
    source: str | None
    scope: frozenset                 # 适用范围（段号）；空=原记录全部范围
    text: str
    rejected: tuple = ()             # 校验不通过的理由；空=已接受


@dataclass
class ClaimFact:
    """转述/主张：不是独立观察，必须与证据核对。"""
    record_id: str
    summary: str
    references: tuple
    text: str


@dataclass
class HandoverFact:
    """签收办理记录。"""
    record_id: str
    date: tuple | None
    segments: tuple
    excluded: tuple
    basis: tuple
    text: str
