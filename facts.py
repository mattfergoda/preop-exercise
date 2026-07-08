"""Intermediate fact models for deterministic pre-op triage policy."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    source: str
    details: str


class DatedSource(BaseModel):
    source: str
    date: str | None = None
    raw_date: str | None = None
    details: str = ""


class LabFact(BaseModel):
    code: str
    source: str
    effective_at: str
    days_before_procedure: int | None = None


class VitalFact(BaseModel):
    type: str
    source: str
    date: str
    systolic: float | int | None = None
    diastolic: float | int | None = None
    value_f: float | int | None = None


class MedicationFact(BaseModel):
    name: str
    source: str
    active: bool | None = None
    is_anticoagulant: bool = False


class StructuredFacts(BaseModel):
    procedure_date: str | None = None
    procedure_risk: str | None = None
    latest_cbc: LabFact | None = None
    latest_cmp: LabFact | None = None
    latest_blood_pressure: VitalFact | None = None
    latest_temperature: VitalFact | None = None
    anticoagulants: list[MedicationFact] = Field(default_factory=list)
    unknown_anticoagulants: list[MedicationFact] = Field(default_factory=list)


class DocumentFinding(BaseModel):
    found: bool = False
    clear: bool = False
    source: str | None = None
    document_date: str | None = None
    excerpt: str | None = None
    reason: str = ""


class DocumentFacts(BaseModel):
    history_and_physical: DocumentFinding = Field(default_factory=DocumentFinding)
    signed_consent: DocumentFinding = Field(default_factory=DocumentFinding)
    anticoagulation_plan: DocumentFinding = Field(default_factory=DocumentFinding)
