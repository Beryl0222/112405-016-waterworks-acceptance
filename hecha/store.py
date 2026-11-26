"""资料包与办理日志的装载、校验与追加。

case.json 是可追加原文的资料包；办理过程（追加记录、解释确认、
局部签认、撤回）写入 journal.json，只追加不删改，原文始终可查。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import model as M
from . import parse

RECORD_KINDS = {"drawing", "pressure-test", "construction", "flushing",
                "correction", "supervision", "request", "handover"}


class CaseView:
    """资料包 + 办理日志的合并视图。撤回只做标记，不删除原文。"""

    def __init__(self, case_id, records, segments, joints, criteria,
                 existing, confirmations, signoffs, withdrawals, problems):
        self.case_id = case_id
        self.records = records
        self.segments = segments
        self.joints = joints
        self.criteria = criteria
        self.existing = existing
        self.confirmations = confirmations    # 日志中的结构化确认
        self.signoffs = signoffs              # 日志中的局部签认
        self.withdrawals = withdrawals        # 被撤回的记录编号
        self.problems = problems              # 装载校验问题

    def active_records(self):
        return [r for r in self.records if r.id not in self.withdrawals]

    def without(self, record_id):
        """模拟撤回某记录后的视图（用于找真正依赖它的签认）。"""
        return CaseView(self.case_id, self.records, self.segments,
                        self.joints, self.criteria, self.existing,
                        self.confirmations, self.signoffs,
                        self.withdrawals | {record_id}, self.problems)

    def criterion(self, kind):
        return next((c for c in self.criteria if c.kind == kind), None)

    def verified_segments(self):
        """已接管或已签认的段。"""
        segs = set()
        for e in self.existing:
            if e.get("state") == "accepted":
                segs.update(e.get("segments", []))
        for s in self.signoffs:
            segs.update(s.get("segments", []))
        return segs

    def record_ids(self):
        return {r.id for r in self.records}


def load_view(case_path, journal_path=None):
    case = json.loads(Path(case_path).read_text(encoding="utf-8"))
    problems = []

    records = [M.Record(r["id"], r["kind"], r["text"],
                        parse.classify_stage(r["kind"], r["text"]))
               for r in case.get("records", [])]
    segments = {s["id"]: M.Segment(s["id"], s["from"], s["to"])
                for s in case["objects"].get("segments", [])}
    joints = {j["id"]: M.Joint(j["id"], tuple(j["joins"]))
              for j in case["objects"].get("joints", [])}
    criteria = []
    for c in case["objects"].get("criteria", []):
        try:
            criteria.append(parse.parse_criterion(c["id"], c["text"]))
        except (KeyError, ValueError) as exc:
            problems.append(str(exc))
    existing = case.get("existing", [])

    confirmations, signoffs, withdrawals = [], [], set()
    if journal_path and Path(journal_path).exists():
        raw = Path(journal_path).read_text(encoding="utf-8").strip()
        journal = json.loads(raw) if raw else {"entries": []}
        for entry in journal.get("entries", []):
            etype = entry.get("type")
            if etype == "record":
                r = entry["record"]
                records.append(M.Record(
                    r["id"], r["kind"], r["text"],
                    parse.classify_stage(r["kind"], r["text"]),
                    origin="journal"))
            elif etype == "confirmation":
                confirmations.append(entry)
            elif etype == "signoff":
                signoffs.append(entry)
            elif etype == "withdrawal":
                withdrawals.add(entry["record"])
            else:
                problems.append(f"日志条目类型未知：{etype}")

    cid = case.get("case_id", "")
    problems += validate(cid, records, segments, joints, existing)
    return CaseView(cid, records, segments, joints,
                    criteria, existing, confirmations, signoffs,
                    withdrawals, problems)


def validate(case_id, records, segments, joints, existing):
    """装载校验：编号唯一、引用有效、登记一致。"""
    problems = []
    if not case_id:
        problems.append("缺少 case_id")
    ids = [r.id for r in records]
    if len(ids) != len(set(ids)):
        problems.append("记录编号重复")
    for jid, joint in joints.items():
        for seg in joint.joins:
            if seg not in segments:
                problems.append(f"接头{jid}引用未知段{seg}")
    record_ids = set(ids)
    for e in existing:
        src = e.get("source")
        if src and src not in record_ids:
            problems.append(f"办理记录{e.get('id')}引用未知原文{src}")
        for seg in e.get("segments", []):
            if seg not in segments:
                problems.append(f"办理记录{e.get('id')}引用未知段{seg}")
    return problems


def append_entry(journal_path, entry):
    """只追加：新记录、确认、签认、撤回都作为新条目写入。"""
    path = Path(journal_path)
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        journal = json.loads(raw) if raw else {"entries": []}
    else:
        journal = {"entries": []}
    journal["entries"].append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(journal, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def next_id(prefix, taken):
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"
