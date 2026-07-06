# Pre-Op Scheduling Triage Design

## Objective

Build an agentic pre-op triage system for Cadence Surgical Center that evaluates a single patient submission and returns the required JSON output:

- `decision`: `READY`, `NEEDS_FOLLOW_UP`, or `NOT_CLEARED`
- `issues[]`: issue category, description, and grounded evidence
- `explanation`: concise deterministic summary

The system must use only the Cadence policy from the assignment. It must not use external medical guidelines.

## Core Architecture Decision

Use the OpenAI Agents SDK for document and fact extraction, but keep final policy adjudication deterministic.

The agent extracts grounded facts from structured and unstructured patient data. It does not decide final clearance.

Only deterministic Python code emits the final `decision`, `issues`, and `explanation`.

## Rationale

The assignment calls for an agentic system and explicitly notes that important information may appear in free-text documents. The OpenAI Agents SDK is a good fit for extracting nuanced document facts such as signed consent status, H&P variants, and ambiguous anticoagulation plans.

The final policy is explicit and should be applied deterministically. This improves:

- correctness against exact policy rules
- repeatability
- evidence grounding
- local testability
- behavior under eval determinism checks

## System Flow

```text
triage_submission(...)
  -> TriageOrchestrator
      -> StructuredDataExtractor
      -> DocumentExtractionAgent
          -> OpenAI Agents SDK
          -> fallback: RegexDocumentExtractor
      -> PolicyEvaluator
      -> EvidenceBuilder
  -> TriageOutput
```

## Components

### TriageOrchestrator

Coordinates the workflow.

Responsibilities:

- validate input as `PatientSubmission`
- run structured data extraction
- run the OpenAI document extraction agent on every case
- fail fast if `OPENAI_API_KEY` is missing unless offline mode is explicitly enabled
- fall back to regex extraction only for offline mode, agent failure, or invalid agent output
- pass validated facts to the policy evaluator
- return a validated `TriageOutput`

### StructuredDataExtractor

Pure Python.

Responsibilities:

- parse procedure date and risk
- identify latest blood pressure and temperature by date
- identify latest CBC and CMP by `effective_at`
- detect active anticoagulant medications
- detect unknown anticoagulant active status
- preserve source paths and indexes for evidence generation

### DocumentExtractionAgent

OpenAI Agents SDK agent.

Runs on every case.

Responsibilities:

- classify document evidence for History and Physical
- classify document evidence for signed surgical consent
- classify document evidence for anticoagulation management plan
- return typed structured facts
- cite document indexes and exact supporting excerpts
- distinguish complete plans from pending, ambiguous, or incomplete language

The agent may not:

- set the final decision
- emit final issues
- override structured fields
- apply external medical knowledge

### RegexDocumentExtractor

Pure Python fallback.

Responsibilities:

- support explicit offline mode
- support tests without network access
- provide resilience if the agent call fails or returns invalid structured output

Regex fallback is not the primary production path.

### PolicyEvaluator

Pure Python.

Responsibilities:

- apply Cadence policy deterministically
- emit all applicable issues in stable order
- determine final decision using precedence: `NOT_CLEARED` if any acute safety exclusion exists, else `NEEDS_FOLLOW_UP` if any required item is missing, stale, ambiguous, or incomplete, else `READY`

### EvidenceBuilder

Pure Python.

Responsibilities:

- generate stable evidence sources such as `procedure.procedure_date`, `labs[0]`, and `documents[2]`
- include exact values, dates, or document excerpts in `evidence.details`
- make every issue grounded to source data

## Model Strategy

Default to the starter repo model, `gpt-4.1-mini`.

Rationale:

- appropriate for extraction and classification
- cost-effective for a prototype
- fast enough for the sample dataset
- configurable if eval results show document-nuance misses

The model remains configurable via `MODEL`.

## Environment Strategy

Normal agentic baseline and eval require `OPENAI_API_KEY`.

If the key is missing:

- default behavior: fail fast with a clear error
- offline behavior: use regex fallback only when explicitly enabled, e.g. `TRIAGE_OFFLINE=1`

The repo should document `uv`'s built-in `.env` support:

```bash
uv run --env-file .env run_baseline.py
```

No code-level dotenv dependency is needed unless non-uv execution becomes a requirement.

## Policy Rules

### Required Documentation

- H&P must be present and within 30 calendar days of procedure date.
- Surgical consent must be present and clearly signed.
- Missing, stale, unsigned, or ambiguous documentation produces `REQUIRED_DOCUMENTATION`.
- Missing structured fields needed to evaluate documentation produces `MISSING_REQUIRED_DATA`.

### Required Testing

- LOW/MODERATE risk: latest CBC within 30 days.
- HIGH risk: latest CBC and latest CMP within 14 days.
- Only the most recent result for each required test type is considered.
- Missing or stale required tests produce `REQUIRED_TESTING`.

### Anticoagulation Management

- Active anticoagulant medication requires a clear perioperative management plan.
- Unknown active status for an anticoagulant produces `MISSING_REQUIRED_DATA`.
- Missing, pending, incomplete, or ambiguous plan produces `ANTICOAGULATION_MANAGEMENT`.

### Acute Safety Exclusions

- Latest systolic BP >= 180 produces `NOT_CLEARED`.
- Latest diastolic BP >= 110 produces `NOT_CLEARED`.
- Latest temperature > 100.4 F produces `NOT_CLEARED`.
- Missing latest BP or temperature produces `MISSING_REQUIRED_DATA`.

## Determinism Strategy

- agent extracts facts only
- deterministic policy code owns final decision
- stable issue ordering
- stable explanation formatting
- Pydantic validation for agent output and final output
- explicit offline fallback for tests and development
- no free-form final LLM response

## Testing Strategy

Unit tests should cover:

- ready case
- missing procedure date
- missing procedure risk
- stale H&P
- unsigned consent
- missing consent
- low/moderate CBC missing or stale
- high-risk CBC/CMP missing or stale
- acute BP exclusion
- acute temperature exclusion
- active anticoagulant without clear plan
- unknown anticoagulant active status
- missing vitals
- invalid agent output fallback
- explicit offline fallback
- missing API key fail-fast behavior

Integration checks:

- run baseline generation
- run eval scoring
- run determinism check
- inspect report failures
- refine evidence formatting where grounding fails

## Constraints

- Keep repo restructuring minimal.
- Preserve `triage_submission(...)` as the public entrypoint.
- Use OpenAI Agents SDK for the primary agentic path.
- Keep generated outputs ignored by git.
- Avoid storing secrets in the repo.
