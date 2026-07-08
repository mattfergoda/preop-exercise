"""Deterministic Cadence policy evaluation for pre-op triage."""

from __future__ import annotations

from datetime import date, datetime

from core import PatientSubmission, TriageIssue, TriageIssueEvidence, TriageOutput
from facts import DocumentFacts, DocumentFinding, LabFact, StructuredFacts


READY_EXPLANATION = "All required documentation, testing, anticoagulation planning, and safety checks are satisfied."


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


def _days_before(procedure_date: date, value: str | None) -> int | None:
    value_date = _parse_date(value)
    if value_date is None:
        return None
    return (procedure_date - value_date).days


def _issue(category: str, description: str, source: str, details: str) -> TriageIssue:
    return TriageIssue(
        category=category,  # type: ignore[arg-type]
        description=description,
        evidence=TriageIssueEvidence(source=source, details=details),
    )


def _document_source(finding: DocumentFinding, fallback: str = "documents") -> str:
    return finding.source or fallback


def _lab_summary(submission: PatientSubmission) -> str:
    parts = []
    for index, lab in enumerate(submission.labs[:5]):
        parts.append(
            f"labs[{index}] code={lab.code or 'null'} display={lab.display or 'null'} effective_at={lab.effective_at or 'null'}"
        )
    return "; ".join(parts) if parts else "no labs submitted"


def _document_summary(submission: PatientSubmission) -> str:
    parts = []
    for index, document in enumerate(submission.documents[:5]):
        excerpt = (document.text or "").strip()[:120]
        parts.append(
            f"documents[{index}] type={document.type or 'null'} date={document.date or 'null'} text={excerpt or 'null'}"
        )
    return "; ".join(parts) if parts else "no documents submitted"


def _lab_issue(
    submission: PatientSubmission,
    lab_name: str,
    lab: LabFact | None,
    risk: str,
    window_days: int,
    procedure_date: date,
) -> TriageIssue | None:
    if lab is None:
        return _issue(
            "REQUIRED_TESTING",
            f"{lab_name} missing",
            "labs",
            f"No {lab_name} result with valid effective_at found for procedure_risk {risk}. Available labs: {_lab_summary(submission)}",
        )
    days = lab.days_before_procedure
    if days is None:
        days = _days_before(procedure_date, lab.effective_at)
    if days is None or days < 0 or days > window_days:
        return _issue(
            "REQUIRED_TESTING",
            f"{lab_name} outside {window_days}-day window for {risk} risk procedure",
            lab.source,
            f"{lab_name} effective_at {lab.effective_at} vs procedure_date {procedure_date.isoformat()} ({days if days is not None else 'unknown'} days prior; must be within {window_days})",
        )
    return None


def evaluate_policy(
    submission: PatientSubmission,
    structured: StructuredFacts,
    documents: DocumentFacts,
) -> TriageOutput:
    """Apply Cadence policy deterministically and emit stable, grounded issues."""

    issues: list[TriageIssue] = []
    procedure_date = _parse_date(structured.procedure_date)
    procedure_risk = structured.procedure_risk

    if procedure_date is None:
        issues.append(
            _issue(
                "MISSING_REQUIRED_DATA",
                "Missing procedure date",
                "procedure.procedure_date",
                "procedure.procedure_date is null",
            )
        )

    if procedure_risk is None:
        issues.append(
            _issue(
                "MISSING_REQUIRED_DATA",
                "Missing procedure risk",
                "procedure.procedure_risk",
                "procedure.procedure_risk is null",
            )
        )

    if procedure_date is not None:
        hp = documents.history_and_physical
        if not hp.found:
            issues.append(
                _issue(
                    "REQUIRED_DOCUMENTATION",
                    "History and Physical document missing",
                    "documents",
                    f"No clear History and Physical document found. Reviewed documents: {_document_summary(submission)}",
                )
            )
        else:
            hp_days = _days_before(procedure_date, hp.document_date)
            if hp_days is None or hp_days < 0 or hp_days > 30:
                date_text = hp.document_date or "unknown"
                issues.append(
                    _issue(
                        "REQUIRED_DOCUMENTATION",
                        "H&P outside 30-day window",
                        _document_source(hp),
                        f"H&P date {date_text} vs procedure_date {procedure_date.isoformat()} ({hp_days if hp_days is not None else 'unknown'} days prior; must be within 30)",
                    )
                )

        consent = documents.signed_consent
        if not consent.found:
            issues.append(
                _issue(
                    "REQUIRED_DOCUMENTATION",
                    "Signed surgical consent missing",
                    "documents",
                    f"No signed surgical consent document found. Reviewed documents: {_document_summary(submission)}",
                )
            )
        elif not consent.clear:
            excerpt = consent.excerpt or consent.reason or "Consent document is ambiguous"
            issues.append(
                _issue(
                    "REQUIRED_DOCUMENTATION",
                    "Surgical consent not clearly signed",
                    _document_source(consent),
                    f"Consent document text does not clearly indicate signed consent: {excerpt}",
                )
            )

    if procedure_date is not None and procedure_risk is not None:
        if procedure_risk in {"LOW", "MODERATE"}:
            issue = _lab_issue(submission, "CBC", structured.latest_cbc, procedure_risk, 30, procedure_date)
            if issue:
                issues.append(issue)
        elif procedure_risk == "HIGH":
            for lab_name, lab in (("CBC", structured.latest_cbc), ("CMP", structured.latest_cmp)):
                issue = _lab_issue(submission, lab_name, lab, procedure_risk, 14, procedure_date)
                if issue:
                    issues.append(issue)

    for medication in structured.unknown_anticoagulants:
        issues.append(
            _issue(
                "MISSING_REQUIRED_DATA",
                "Unknown anticoagulant active status",
                medication.source,
                f"Medication {medication.name} has active=null; cannot determine if currently taking",
            )
        )

    if structured.anticoagulants and not documents.anticoagulation_plan.clear:
        medication = structured.anticoagulants[0]
        plan = documents.anticoagulation_plan
        source = plan.source or medication.source
        issues.append(
            _issue(
                "ANTICOAGULATION_MANAGEMENT",
                "Missing perioperative anticoagulation plan",
                source,
                f"Active anticoagulant medication {medication.name} present ({medication.source}) but no clear perioperative plan document found. Document evidence: {plan.excerpt or plan.reason or 'no anticoagulation plan document found'}",
            )
        )

    bp = structured.latest_blood_pressure
    if bp is None:
        issues.append(
            _issue(
                "MISSING_REQUIRED_DATA",
                "Missing latest blood pressure",
                "vitals",
                "No blood_pressure vital with valid date found",
            )
        )

    temp = structured.latest_temperature
    if temp is None:
        issues.append(
            _issue(
                "MISSING_REQUIRED_DATA",
                "Missing latest temperature",
                "vitals",
                "No temperature vital with valid date found",
            )
        )

    if bp is not None and ((bp.systolic is not None and bp.systolic >= 180) or (bp.diastolic is not None and bp.diastolic >= 110)):
        issues.append(
            _issue(
                "ACUTE_SAFETY_EXCLUSION",
                "Blood pressure meets exclusion threshold",
                bp.source,
                f"latest BP date={bp.date}, systolic={bp.systolic}, diastolic={bp.diastolic}; threshold systolic>=180 or diastolic>=110",
            )
        )

    if temp is not None and temp.value_f is not None and temp.value_f > 100.4:
        issues.append(
            _issue(
                "ACUTE_SAFETY_EXCLUSION",
                "Temperature exceeds exclusion threshold",
                temp.source,
                f"latest temperature date={temp.date}, value_f={temp.value_f}; threshold is > 100.4",
            )
        )

    if any(issue.category == "ACUTE_SAFETY_EXCLUSION" for issue in issues):
        decision = "NOT_CLEARED"
    elif issues:
        decision = "NEEDS_FOLLOW_UP"
    else:
        decision = "READY"

    explanation = READY_EXPLANATION if not issues else " | ".join(
        f"{issue.category}: {issue.description}" for issue in issues
    )
    return TriageOutput(decision=decision, issues=issues, explanation=explanation)
