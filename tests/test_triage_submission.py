from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import document_extraction
from core import PatientSubmission, TriageOutput, triage_submission
from document_extraction import extract_document_facts, extract_document_facts_with_regex
from facts import DocumentFacts, DocumentFinding
from policy import evaluate_policy
from structured_extraction import extract_structured_facts


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ready_payload() -> dict[str, object]:
    return {
        "patient": {"id": "patient-1"},
        "procedure": {
            "case_id": "case-1",
            "procedure_risk": "LOW",
            "procedure_date": "2026-03-01",
        },
        "vitals": [
            {
                "type": "blood_pressure",
                "systolic": 120,
                "diastolic": 80,
                "date": "2026-02-25T10:00:00Z",
            },
            {"type": "temperature", "value_f": 98.8, "date": "2026-02-25"},
        ],
        "labs": [
            {
                "code": "CBC",
                "display": "Complete Blood Count",
                "effective_at": "2026-02-20T09:00:00Z",
                "status": "final",
            }
        ],
        "medications": [],
        "conditions": [],
        "documents": [
            {
                "type": "History and Physical",
                "date": "2026-02-20",
                "text": "HISTORY AND PHYSICAL: pre-op evaluation complete.",
            },
            {
                "type": "Surgical Consent",
                "date": "2026-02-22",
                "text": "Electronic consent obtained and signed by patient for procedure.",
            },
        ],
    }


def test_structured_extraction_selects_latest_labs_vitals_and_anticoagulants(
    ready_payload: dict[str, object],
) -> None:
    ready_payload["procedure"] = {
        "procedure_risk": "HIGH",
        "procedure_date": "2026-03-10",
    }
    ready_payload["labs"] = [
        {"code": "CBC", "display": "Complete Blood Count", "effective_at": "2026-02-20"},
        {"code": "LAB-CBC", "display": "Complete Blood Count Panel", "effective_at": "2026-03-01"},
        {"code": "LAB-CMP", "display": "Comprehensive Metabolic Panel", "effective_at": "2026-02-28T09:00:00Z"},
    ]
    ready_payload["vitals"] = [
        {"type": "blood_pressure", "systolic": 130, "diastolic": 80, "date": "2026-02-28"},
        {"type": "blood_pressure", "systolic": 140, "diastolic": 85, "date": "2026-03-02"},
        {"type": "temperature", "value_f": 99.1, "date": "2026-03-02"},
    ]
    ready_payload["medications"] = [
        {"name": "apixaban", "active": True},
        {"name": "warfarin", "active": None},
        {"name": "rivaroxaban", "active": False},
    ]

    facts = extract_structured_facts(PatientSubmission.model_validate(ready_payload))

    assert facts.latest_cbc is not None
    assert facts.latest_cbc.source == "labs[1]"
    assert facts.latest_cbc.days_before_procedure == 9
    assert facts.latest_cmp is not None
    assert facts.latest_cmp.source == "labs[2]"
    assert facts.latest_blood_pressure is not None
    assert facts.latest_blood_pressure.source == "vitals[1]"
    assert [med.source for med in facts.anticoagulants] == ["medications[0]"]
    assert [med.source for med in facts.unknown_anticoagulants] == ["medications[1]"]


def test_regex_document_extraction_is_conservative_for_malformed_hp_type() -> None:
    row = _sample_row(2)
    facts = extract_document_facts_with_regex(row.submission)

    assert facts.history_and_physical.source == "documents[1]"
    assert facts.history_and_physical.document_date == "2026-01-30"
    assert facts.signed_consent.clear is True


def test_regex_document_extraction_rejects_unsigned_consent(
    ready_payload: dict[str, object],
) -> None:
    ready_payload["documents"] = [
        ready_payload["documents"][0],
        {
            "type": "Surgical Consent",
            "date": "2026-02-22",
            "text": "Consent documented but unsigned; awaiting patient signature.",
        },
    ]

    facts = extract_document_facts_with_regex(PatientSubmission.model_validate(ready_payload))

    assert facts.signed_consent.found is True
    assert facts.signed_consent.clear is False
    assert facts.signed_consent.source == "documents[1]"


def test_policy_ready_case_returns_ready(ready_payload: dict[str, object]) -> None:
    submission = PatientSubmission.model_validate(ready_payload)
    output = evaluate_policy(
        submission,
        extract_structured_facts(submission),
        extract_document_facts_with_regex(submission),
    )

    assert output == TriageOutput(
        decision="READY",
        issues=[],
        explanation="All required documentation, testing, anticoagulation planning, and safety checks are satisfied.",
    )


@pytest.mark.parametrize(
    ("record_index", "decision", "categories"),
    [
        (0, "NEEDS_FOLLOW_UP", ["MISSING_REQUIRED_DATA", "ANTICOAGULATION_MANAGEMENT"]),
        (2, "NOT_CLEARED", ["REQUIRED_DOCUMENTATION", "ACUTE_SAFETY_EXCLUSION"]),
        (3, "NOT_CLEARED", ["ACUTE_SAFETY_EXCLUSION"]),
        (4, "NEEDS_FOLLOW_UP", ["ANTICOAGULATION_MANAGEMENT", "MISSING_REQUIRED_DATA", "MISSING_REQUIRED_DATA"]),
        (5, "NEEDS_FOLLOW_UP", ["REQUIRED_TESTING"]),
        (8, "NEEDS_FOLLOW_UP", ["REQUIRED_DOCUMENTATION"]),
        (10, "NEEDS_FOLLOW_UP", ["REQUIRED_TESTING", "REQUIRED_TESTING"]),
        (12, "READY", []),
        (14, "NEEDS_FOLLOW_UP", ["MISSING_REQUIRED_DATA"]),
    ],
)
def test_sample_regressions_offline(
    monkeypatch: pytest.MonkeyPatch,
    record_index: int,
    decision: str,
    categories: list[str],
) -> None:
    monkeypatch.setenv("TRIAGE_OFFLINE", "1")
    row = _sample_row(record_index)

    output = triage_submission(row.submission, model="test-model")

    assert output.decision == decision
    assert [issue.category for issue in output.issues] == categories


def test_triage_submission_orchestrates_agent_extraction_by_default(
    monkeypatch: pytest.MonkeyPatch,
    ready_payload: dict[str, object],
) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_extract(
        submission: PatientSubmission,
        *,
        model: str,
        offline: bool,
    ) -> DocumentFacts:
        calls.append((model, offline))
        return DocumentFacts(
            history_and_physical=DocumentFinding(found=True, clear=True, source="documents[0]", document_date="2026-02-20"),
            signed_consent=DocumentFinding(found=True, clear=True, source="documents[1]", document_date="2026-02-22"),
        )

    monkeypatch.delenv("TRIAGE_OFFLINE", raising=False)
    monkeypatch.setattr(document_extraction, "extract_document_facts", fake_extract)

    output = triage_submission(ready_payload, model="agent-model")

    assert output.decision == "READY"
    assert calls == [("agent-model", False)]


def test_missing_api_key_fails_fast_without_offline(
    monkeypatch: pytest.MonkeyPatch,
    ready_payload: dict[str, object],
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TRIAGE_OFFLINE", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY.*TRIAGE_OFFLINE=1"):
        triage_submission(ready_payload, model="test-model")


def test_offline_mode_does_not_require_api_key(
    monkeypatch: pytest.MonkeyPatch,
    ready_payload: dict[str, object],
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TRIAGE_OFFLINE", "1")

    output = triage_submission(ready_payload, model="test-model")

    assert output.decision == "READY"


def test_invalid_agent_output_falls_back_to_regex(
    monkeypatch: pytest.MonkeyPatch,
    ready_payload: dict[str, object],
) -> None:
    class FakeRunner:
        @staticmethod
        def run_sync(agent: object, payload: str) -> object:
            return SimpleNamespace(final_output={"history_and_physical": "invalid"})

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("TRIAGE_OFFLINE", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "agents",
        SimpleNamespace(
            Agent=lambda **kwargs: kwargs,
            ModelSettings=lambda **kwargs: kwargs,
            Runner=FakeRunner,
        ),
    )

    facts = extract_document_facts(
        PatientSubmission.model_validate(ready_payload),
        model="test-model",
        offline=False,
    )

    assert facts.history_and_physical.source == "documents[0]"
    assert facts.signed_consent.clear is True


def _sample_row(record_index: int) -> object:
    with (ROOT / "data" / "patients_sample_50.jsonl").open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index == record_index:
                payload = json.loads(line)
                return SimpleNamespace(
                    case_id=payload["case_id"],
                    submission=PatientSubmission.model_validate(payload["submission"]),
                    expected_output=TriageOutput.model_validate(payload["expected_output"]),
                )
    raise IndexError(record_index)
