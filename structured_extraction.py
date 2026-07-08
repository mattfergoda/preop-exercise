"""Deterministic extraction of structured patient submission facts."""

from __future__ import annotations

from datetime import date, datetime

from core import PatientSubmission
from facts import LabFact, MedicationFact, StructuredFacts, VitalFact


ANTICOAGULANTS = {
    "apixaban",
    "warfarin",
    "rivaroxaban",
    "dabigatran",
    "edoxaban",
    "heparin",
    "enoxaparin",
}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _days_before(procedure_date: date | None, value: str | None) -> int | None:
    value_date = _parse_date(value)
    if procedure_date is None or value_date is None:
        return None
    return (procedure_date - value_date).days


def _is_cbc(code: str | None, display: str | None) -> bool:
    code_text = (code or "").strip().upper()
    display_text = (display or "").lower()
    return code_text in {"CBC", "LAB-CBC"} or "complete blood count" in display_text or "cbc" in display_text


def _is_cmp(code: str | None, display: str | None) -> bool:
    code_text = (code or "").strip().upper()
    display_text = (display or "").lower()
    return code_text in {"CMP", "LAB-CMP"} or "comprehensive metabolic panel" in display_text or "cmp" in display_text


def _latest_lab(
    submission: PatientSubmission,
    procedure_date: date | None,
    predicate: object,
) -> LabFact | None:
    candidates: list[tuple[date, LabFact]] = []
    for index, lab in enumerate(submission.labs):
        lab_date = _parse_date(lab.effective_at)
        if lab_date is None:
            continue
        if not predicate(lab.code, lab.display):  # type: ignore[operator]
            continue
        candidates.append(
            (
                lab_date,
                LabFact(
                    code=(lab.code or lab.display or "").strip(),
                    source=f"labs[{index}]",
                    effective_at=lab.effective_at or "",
                    days_before_procedure=_days_before(procedure_date, lab.effective_at),
                ),
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _latest_vital(submission: PatientSubmission, vital_type: str) -> VitalFact | None:
    candidates: list[tuple[date, VitalFact]] = []
    for index, vital in enumerate(submission.vitals):
        if (vital.type or "").lower() != vital_type:
            continue
        vital_date = _parse_date(vital.date)
        if vital_date is None or not vital.date:
            continue
        candidates.append(
            (
                vital_date,
                VitalFact(
                    type=vital_type,
                    source=f"vitals[{index}]",
                    date=vital.date,
                    systolic=getattr(vital, "systolic", None),
                    diastolic=getattr(vital, "diastolic", None),
                    value_f=getattr(vital, "value_f", None),
                ),
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def extract_structured_facts(submission: PatientSubmission) -> StructuredFacts:
    """Extract structured facts without inferring from document text."""

    procedure_date_raw = submission.procedure.procedure_date if submission.procedure else None
    procedure_date = _parse_date(procedure_date_raw)
    procedure_risk = submission.procedure.procedure_risk if submission.procedure else None

    anticoagulants: list[MedicationFact] = []
    unknown_anticoagulants: list[MedicationFact] = []
    for index, medication in enumerate(submission.medications):
        name = medication.name or ""
        if name.strip().lower() not in ANTICOAGULANTS:
            continue
        fact = MedicationFact(
            name=name,
            source=f"medications[{index}]",
            active=medication.active,
            is_anticoagulant=True,
        )
        if medication.active is True:
            anticoagulants.append(fact)
        elif medication.active is None:
            unknown_anticoagulants.append(fact)

    return StructuredFacts(
        procedure_date=procedure_date_raw,
        procedure_risk=procedure_risk,
        latest_cbc=_latest_lab(submission, procedure_date, _is_cbc),
        latest_cmp=_latest_lab(submission, procedure_date, _is_cmp),
        latest_blood_pressure=_latest_vital(submission, "blood_pressure"),
        latest_temperature=_latest_vital(submission, "temperature"),
        anticoagulants=anticoagulants,
        unknown_anticoagulants=unknown_anticoagulants,
    )
