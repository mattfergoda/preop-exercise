"""Agentic and deterministic fallback extraction for document facts."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime
from typing import Any

from core import PatientSubmission
from facts import DocumentFacts, DocumentFinding
from structured_extraction import ANTICOAGULANTS


LOGGER = logging.getLogger(__name__)


DOCUMENT_EXTRACTION_INSTRUCTIONS = """
You extract document facts for Cadence Surgical Center pre-op scheduling.
Use only these policy concepts: History and Physical within context, signed surgical consent, and a clear perioperative anticoagulation management plan.
Extract facts only. Do not decide READY, NEEDS_FOLLOW_UP, or NOT_CLEARED. Do not emit issue categories.
Return exact documents[i] source paths and exact excerpts from document text when possible.
For H&P, identify the best clearly valid H&P document and include its date. Prefer authoritative document type signals; use text as supporting evidence. Be conservative with malformed or ambiguous document types.
For consent, clear=true only when the document clearly says signed, signature on file, signed consent scanned, electronically signed, or equivalent. clear=false for unsigned, awaiting signature, pending, or counseling without signature.
For anticoagulation plans, clear=true only when the document clearly describes before-procedure and after-procedure medication management. clear=false for pending, follow up, final plan pending, no hold/resume guidance, not yet documented, or ambiguous language.
""".strip()


_H_AND_P_RE = re.compile(
    r"\b(h\s*&\s*p|h\s+and\s+p|h\s*/\s*p|h\s*\+\s*p|history\s*(?:and|&)\s*physical|hist\s*&?\s*phys)\b",
    re.I,
)
_H_AND_P_TEXT_RE = re.compile(
    r"\b(h\s*&\s*p|h\s+and\s+p|history\s*(?:and|&)\s*physical|history:.*physical exam|physical exam recorded|hpi:.*pe documented)\b",
    re.I | re.S,
)
_CONSENT_RE = re.compile(r"\bconsent\b", re.I)
_CONSENT_REJECT_RE = re.compile(
    r"\b(unsigned|awaiting signature|pending signature|not signed|without signature)\b",
    re.I,
)
_CONSENT_SIGNED_RE = re.compile(
    r"\b(signed|signature on file|signed consent scanned|electronically signed|electronic consent obtained and signed|consent obtained and signed)\b",
    re.I,
)
_ANTICOAG_REJECT_RE = re.compile(
    r"\b(pending|follow up|not yet documented|to be finalized|no clear hold/resume|no clear guidance|details not yet documented|final plan pending)\b",
    re.I,
)
_ANTICOAG_REF_RE = re.compile(
    r"\b(anticoag|blood thinner|apixaban|warfarin|rivaroxaban|dabigatran|edoxaban|heparin|enoxaparin)\b",
    re.I,
)
_ANTICOAG_BEFORE_RE = re.compile(r"\b(hold|stop|stopped|discontinue|withhold).{0,60}\b(before|prior|pre-?op|preprocedure|pre-procedure)\b|\b(before|prior|pre-?op|preprocedure|pre-procedure).{0,60}\b(hold|stop|stopped|discontinue|withhold)\b", re.I)
_ANTICOAG_AFTER_RE = re.compile(r"\b(resume|restart|restarted).{0,60}\b(after|post-?op|postprocedure|post-procedure)\b|\b(after|post-?op|postprocedure|post-procedure).{0,60}\b(resume|restart|restarted)\b", re.I)


def _excerpt(text: str | None) -> str | None:
    if not text:
        return None
    return text.strip()[:240]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _source_index(source: str | None) -> int | None:
    if not source:
        return None
    match = re.fullmatch(r"documents\[(\d+)\]", source.strip())
    return int(match.group(1)) if match else None


def _document_payload(submission: PatientSubmission) -> dict[str, Any]:
    active_anticoagulants = []
    for index, medication in enumerate(submission.medications):
        name = medication.name or ""
        if medication.active is True and name.lower() in ANTICOAGULANTS:
            active_anticoagulants.append({"name": name, "source": f"medications[{index}]"})
    return {
        "procedure": {
            "procedure_date": submission.procedure.procedure_date if submission.procedure else None,
            "procedure_risk": submission.procedure.procedure_risk if submission.procedure else None,
        },
        "active_anticoagulants": active_anticoagulants,
        "documents": [
            {
                "index": index,
                "type": document.type,
                "date": document.date,
                "author": document.author,
                "text": document.text,
            }
            for index, document in enumerate(submission.documents)
        ],
    }


def extract_document_facts(
    submission: PatientSubmission,
    *,
    model: str,
    offline: bool,
) -> DocumentFacts:
    """Extract document facts with the Agents SDK, falling back to regex on failure."""

    if offline:
        return extract_document_facts_with_regex(submission)
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Set OPENAI_API_KEY or use TRIAGE_OFFLINE=1 for deterministic offline extraction")

    try:
        from agents import Agent, ModelSettings, Runner

        try:
            agent = Agent(
                name="Cadence Pre-Op Document Extraction Agent",
                instructions=DOCUMENT_EXTRACTION_INSTRUCTIONS,
                model=model,
                model_settings=ModelSettings(temperature=0),
                output_type=DocumentFacts,
            )
        except TypeError:
            agent = Agent(
                name="Cadence Pre-Op Document Extraction Agent",
                instructions=DOCUMENT_EXTRACTION_INSTRUCTIONS,
                model=model,
                output_type=DocumentFacts,
            )
        payload = json.dumps(_document_payload(submission), sort_keys=True)
        result = Runner.run_sync(agent, payload)
        facts = DocumentFacts.model_validate(result.final_output)
        return _validate_agent_facts(submission, facts)
    except Exception:
        LOGGER.warning("Document extraction agent failed; using regex fallback", exc_info=True)
        return extract_document_facts_with_regex(submission)


def _is_malformed_hp_type(document_type: str) -> bool:
    text = document_type.lower()
    return "phsyical" in text or ("history" in text and "physical" not in text and not _H_AND_P_RE.search(text))


def _is_hp_candidate(document_type: str, text: str) -> bool:
    if _is_malformed_hp_type(document_type):
        return False
    if _H_AND_P_RE.search(document_type):
        return True
    generic_type = re.search(r"\b(pre-?op|preoperative|admission|consult|clearance|evaluation|scanned|surgical)\b", document_type, re.I)
    return bool(generic_type and _H_AND_P_TEXT_RE.search(text))


def _extract_hp(submission: PatientSubmission) -> DocumentFinding:
    candidates: list[tuple[str, int, DocumentFinding]] = []
    for index, document in enumerate(submission.documents):
        document_type = document.type or ""
        text = document.text or ""
        if not _is_hp_candidate(document_type, text):
            continue
        date_key = document.date or ""
        candidates.append(
            (
                date_key,
                index,
                DocumentFinding(
                    found=True,
                    clear=True,
                    source=f"documents[{index}]",
                    document_date=document.date,
                    excerpt=_excerpt(document.text),
                    reason="Document type/text supports History and Physical",
                ),
            )
        )
    if not candidates:
        return DocumentFinding(found=False, clear=False, reason="No clear History and Physical document found")
    return max(candidates, key=lambda item: (item[0], -item[1]))[2]


def _extract_consent(submission: PatientSubmission) -> DocumentFinding:
    unclear: DocumentFinding | None = None
    for index, document in enumerate(submission.documents):
        document_type = document.type or ""
        text = document.text or ""
        combined = f"{document_type}\n{text}"
        if not (_CONSENT_RE.search(document_type) or _CONSENT_RE.search(text)):
            continue
        finding = DocumentFinding(
            found=True,
            clear=False,
            source=f"documents[{index}]",
            document_date=document.date,
            excerpt=_excerpt(document.text),
            reason="Consent document does not clearly indicate signed consent",
        )
        if _CONSENT_REJECT_RE.search(text):
            return finding
        if _CONSENT_SIGNED_RE.search(combined):
            return finding.model_copy(update={"clear": True, "reason": "Consent clearly signed"})
        if unclear is None:
            unclear = finding
    return unclear or DocumentFinding(found=False, clear=False, reason="No surgical consent document found")


def _extract_anticoagulation_plan(submission: PatientSubmission) -> DocumentFinding:
    best_unclear: DocumentFinding | None = None
    for index, document in enumerate(submission.documents):
        text = document.text or ""
        combined = f"{document.type or ''}\n{text}"
        if not _ANTICOAG_REF_RE.search(combined):
            continue
        finding = DocumentFinding(
            found=True,
            clear=False,
            source=f"documents[{index}]",
            document_date=document.date,
            excerpt=_excerpt(document.text),
            reason="Anticoagulation plan is missing, pending, incomplete, or ambiguous",
        )
        if _ANTICOAG_REJECT_RE.search(text):
            return finding
        if _ANTICOAG_BEFORE_RE.search(text) and _ANTICOAG_AFTER_RE.search(text):
            return finding.model_copy(update={"clear": True, "reason": "Plan describes before- and after-procedure anticoagulant management"})
        if best_unclear is None:
            best_unclear = finding
    return best_unclear or DocumentFinding(found=False, clear=False, reason="No perioperative anticoagulation plan document found")


def _validate_agent_facts(submission: PatientSubmission, facts: DocumentFacts) -> DocumentFacts:
    """Keep agent output, but enforce conservative deterministic document signals."""

    regex_facts = extract_document_facts_with_regex(submission)

    hp = facts.history_and_physical
    hp_index = _source_index(hp.source)
    replace_hp = not hp.found
    if hp_index is not None and hp_index < len(submission.documents):
        document = submission.documents[hp_index]
        replace_hp = replace_hp or not _is_hp_candidate(document.type or "", document.text or "")
    if regex_facts.history_and_physical.found:
        agent_date = _parse_date(hp.document_date)
        regex_date = _parse_date(regex_facts.history_and_physical.document_date)
        replace_hp = replace_hp or (
            agent_date is not None and regex_date is not None and regex_date > agent_date
        )
    if replace_hp:
        hp = regex_facts.history_and_physical

    consent = facts.signed_consent
    if regex_facts.signed_consent.found:
        if regex_facts.signed_consent.clear or not consent.found:
            consent = regex_facts.signed_consent
        elif not regex_facts.signed_consent.clear and _CONSENT_REJECT_RE.search(
            regex_facts.signed_consent.excerpt or ""
        ):
            consent = regex_facts.signed_consent

    anticoagulation = facts.anticoagulation_plan
    if regex_facts.anticoagulation_plan.found and not regex_facts.anticoagulation_plan.clear:
        anticoagulation = regex_facts.anticoagulation_plan

    return DocumentFacts(
        history_and_physical=hp,
        signed_consent=consent,
        anticoagulation_plan=anticoagulation,
    )


def extract_document_facts_with_regex(submission: PatientSubmission) -> DocumentFacts:
    """Conservative deterministic document extraction used offline and as fallback."""

    return DocumentFacts(
        history_and_physical=_extract_hp(submission),
        signed_consent=_extract_consent(submission),
        anticoagulation_plan=_extract_anticoagulation_plan(submission),
    )
