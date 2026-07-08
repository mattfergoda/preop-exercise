# Submission Notes

## Summary

This submission implements `triage_submission(...)` as an agentic pre-op triage system with deterministic final policy evaluation.

The normal path uses the OpenAI Agents SDK to extract document facts from free-text clinical documents. Deterministic Python code extracts structured facts, applies the Cadence policy, builds grounded evidence, and emits the final JSON output.

The implementation intentionally separates fact extraction from policy judgment. The agent can identify and cite relevant document evidence, but it cannot decide whether a patient is `READY`, `NEEDS_FOLLOW_UP`, or `NOT_CLEARED`.

## Requirements Coverage

The assignment asks for a system that determines whether a patient can be scheduled and what items are missing or blocking. This implementation covers the policy rules in the assignment appendix:

- Required H&P within 30 days of the procedure date
- Required signed surgical consent
- LOW/MODERATE risk CBC within 30 days
- HIGH risk CBC and CMP within 14 days
- Active anticoagulant medication requiring a clear perioperative management plan
- Acute exclusion for systolic BP >= 180 mmHg
- Acute exclusion for diastolic BP >= 110 mmHg
- Acute exclusion for temperature > 100.4 F
- Missing or unknown required fields resulting in `NEEDS_FOLLOW_UP`

The output remains the required schema:

```json
{
  "decision": "READY | NEEDS_FOLLOW_UP | NOT_CLEARED",
  "issues": [
    {
      "category": "...",
      "description": "...",
      "evidence": {
        "source": "...",
        "details": "..."
      }
    }
  ],
  "explanation": "..."
}
```

## Architecture

```text
triage_submission(...)
  -> StructuredDataExtractor
  -> DocumentExtractionAgent
      -> OpenAI Agents SDK
      -> regex fallback for offline/failure mode
  -> PolicyEvaluator
  -> TriageOutput
```

Key files:

- `core.py`: public schema and `triage_submission(...)` entrypoint
- `facts.py`: intermediate fact models
- `structured_extraction.py`: deterministic extraction for procedure, vitals, labs, and medications
- `document_extraction.py`: OpenAI Agents SDK document extraction plus deterministic fallback
- `policy.py`: deterministic Cadence policy evaluator and evidence builder
- `tests/test_triage_submission.py`: unit and regression tests
- `design-doc.md`: high-level design rationale
- `implementation-plan.md`: implementation handoff plan

## Major Design Decisions

### Agentic Extraction, Deterministic Judgment

The assignment calls for an agentic system, and the input includes free-text documents. The agent is therefore used where it is strongest: extracting nuanced document facts such as H&P candidates, signed consent status, and anticoagulation-plan clarity.

The final policy is explicit, so final adjudication is deterministic. This reduces nondeterminism, improves repeatability, and ensures the final result is grounded in the assignment policy rather than external model reasoning.

### Run the Agent on Every Normal Case

The OpenAI document extraction agent runs on every case in the normal path. Regex extraction is intentionally not used as a pre-screen because that would make brittle heuristics decide whether the agent gets to see a case.

Regex extraction exists only for explicit offline mode and for resilience if the agent call fails or returns invalid structured output.

### Fail Fast Without an API Key

Normal mode requires `OPENAI_API_KEY`. If the key is missing, the system raises a clear error unless `TRIAGE_OFFLINE=1` is explicitly set.

This avoids silently running a non-agentic path when the expected submission behavior is the agentic path.

### Evidence Grounding

Every issue includes a concrete source path and details containing source values, dates, or document excerpts. This makes outputs inspectable and aligns with the assignment requirement that every issue cite exact evidence.

### Typed Agent Output And Validation

The document extraction agent returns a typed `DocumentFacts` object through the Agents SDK structured-output path. The result is validated with Pydantic before deterministic policy code consumes it.

This prevents malformed or free-form agent responses from leaking into final decisions. If agent output is invalid, the system falls back to deterministic document extraction rather than producing a partially trusted result.

## Assumptions

- Date windows are inclusive: within 30 days means `<= 30` calendar days, and within 14 days means `<= 14` calendar days.
- Procedure date and procedure risk must come from structured fields. They are not inferred from document text when the structured field is missing.
- Only the most recent valid result for each required lab type is considered.
- Only the latest valid blood pressure and latest valid temperature are considered for acute safety review.
- Anticoagulant detection is based on a fixed policy-local medication list in `structured_extraction.py`.
- Unknown active status for an anticoagulant is treated as missing required data.
- Offline mode is for local debugging and tests, not the primary submission path.

## Running The Submission

Confirm `uv` is installed:

```bash
uv --version
```

Set an API key for normal agentic execution:

```bash
export OPENAI_API_KEY="<your_api_key>"
```

Alternatively, use a local `.env` file with `uv` env-file support:

```bash
make baseline-env
```

Run tests:

```bash
make test
```

Run baseline inference:

```bash
make baseline
```

Run eval scoring:

```bash
make evals
```

Run determinism check:

```bash
make determinism
```

Print score:

```bash
make score
```

Offline debugging mode:

```bash
TRIAGE_OFFLINE=1 make baseline
```

## Verification Results

Current local verification:

- `make test`: 17 passed
- `make score`: 100.0
- JSON schema valid: 100%
- Decision match oracle: 100%
- Issue categories match oracle: 100%
- Evidence grounding: 100%
- Failed records: 0

Generated reports:

- `data/eval_report.json`
- `data/determinism_report.json`

The determinism report shows 100% decision stability, 100% JSON-format stability, and 100% exact-output match for the repeated case.

## Reflection

The central engineering tradeoff was how much authority to give the LLM. A pure LLM solution would be simpler, but it would make policy enforcement and determinism harder. A pure rules solution would be highly repeatable, but it would make nuanced free-text interpretation and evidence extraction brittle, especially for ambiguous consent language, H&P variants, and incomplete anticoagulation plans.

The chosen approach uses the OpenAI Agents SDK for the unstructured extraction problem and deterministic code for the explicit policy problem. This keeps the system agentic where the input is ambiguous and deterministic where the requirements are strict.

The implementation also treats evidence as a first-class output, not an afterthought. Source paths and concrete details are built into issue generation so reviewers can inspect why each decision was made.

## Limitations And Future Work

- The sample score is 100%, but a larger hidden dataset may contain document phrasings not represented in the sample.
- The anticoagulant list is explicit and policy-local; a production version would use a maintained medication taxonomy.
- The current agent extracts document facts only. A production system could add tracing, confidence reporting, and human-review routing for ambiguous documents.
- The eval harness validates categories and grounding, but it does not fully assess clinical workflow usability or reviewer experience.
- The implementation is optimized for the take-home scope rather than persistence, audit logging, or multi-user deployment.
