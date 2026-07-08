# MVP-8 — Generic per-task relationship classification (R3)

**Date:** 2026-07-08 · **Approved:** user (full R3 branch) · **Lineage:** 492bae60177b
**Supersedes hardcoded assumptions:** `_INTERFACE_WORD_ALT` enum · fixed `"Integration"`
section/label · `_INTEGRATION_SECTION_RE = r"(?i)integrat"`.

## Problem

The pipeline bakes in three domain assumptions that a *general* research-report
generator must not make:

1. **Interface enum** — `api|cli|mcp|sdk|extension|plugin|webhook|connector|interface`
   assumes subjects relate through a *software interface*. False for non-software
   subjects, and incomplete even within software (gRPC, GraphQL, queue, shared DB).
2. **"Integration" as the relationship** — `_resolve_relationship` prompts only for
   "which surface of A could drive/host/call B", the section is literally named
   `"X and Y Integration"`, and the fallback edge label is `"integration"`. Not every
   pair integrates; some **compete**, some are **alternatives**, some are **independent**.
3. Downstream flows (diagram, code, section, summary) all assume cooperation.

User principle (verbatim): *"why 'integration'? not all research contain integration.
consider to use LLM, this is more accurate."*

## Design

### 1. Relationship classification (FRAME)

Replace `_resolve_relationship → (mechanism, verify_terms)` with a structured result:

```
@dataclass(frozen=True)
class Relationship:
    kind: str            # cooperates | competes | extends | alternative | independent | unknown
    descriptor: str      # short noun phrase for the section title, e.g. "Integration",
                         # "Comparison", "Extension model", "Alternatives" — LLM-derived,
                         # NOT a fixed string
    mechanism: str       # short hypothesis sentence (cooperate/extends) OR comparison
                         # framing (competes/alternative); "" for independent/unknown
    mechanism_terms: list[str]   # per-task terms to corroborate/ground against claims
                                 # (replaces the interface enum entirely)
```

- One LLM call over the resolved descriptors + requirement. Prompt asks it to
  CLASSIFY the relationship kind first, then give descriptor + mechanism +
  terms appropriate to that kind. No interface vocabulary in the prompt.
- **Fail-open** to `Relationship("unknown", "Relationship", "", [])` on no judge,
  <2 subjects, parse miss, or exception (never stalls research).
- Grounding is unchanged in spirit: the hypothesis DIRECTS search; only
  claim-corroborated content ships.

### 2. Downstream branches on `kind`

| kind | section descriptor | diagram | code | summary |
|---|---|---|---|---|
| cooperates / extends | Integration / Extension | cluster + cross-edge (grounded in `mechanism_terms` ∩ joint claims) | synthesized usage (grounded in `mechanism_terms`) | states the cooperation conclusion |
| competes / alternative | Comparison / Alternatives | NO integration diagram — per-subject arch diagrams only | NO integration code — a **comparison table** (axes + pros/cons from claims) | states the trade-off conclusion |
| independent | (no forced relationship section) | per-subject only | none | notes independence |
| unknown | Relationship (neutral) | cross-edge only if a joint claim grounds one | usage only if grounded | neutral |

### 3. Mechanism grounding (replaces the enum)

- Cross-edge label: a term from `mechanism_terms` corroborated in joint claims;
  else a generic label from `descriptor`; else no edge. NEVER an enum match.
- Integration-code interfaces: `_grounded_interface_words` becomes
  `_grounded_mechanism_terms(claims, relationship.mechanism_terms)` — the terms
  the code may use are the per-task terms that appear in claims, not a fixed list.
- `_INTERFACE_WORD_ALT` / `_INTERFACE_WORD_RE` / `_INTERFACE_WORD_PLURAL_RE`
  deleted once no consumer references them.

### 4. Section naming + ordering

- `_ensure_integration_section` → `_ensure_relationship_section(sections, subjects, relationship)`:
  inserts `"{A} and {B}: {descriptor}"` before References for kinds that warrant
  a relationship section (all but `independent`); no-op otherwise.
- `_INTEGRATION_SECTION_RE` (`r"(?i)integrat"`) → matches on the descriptor set,
  not the literal word.
- **Ordering fix (v44 defect):** ASSEMBLE enforces References-last — any section
  (model-emitted heading or relationship section) that lands after `## References`
  is moved before it. Deterministic, post-WRITE.

### 5. Blocklist consolidation

`_FEATURE_STOPWORDS` (research_first) and `_GENERIC_LABELS` (diagram_render)
single-sourced where the LLM+claim grounding does not already replace them.

## Non-goals (this slice)

- No change to disambiguation, RESEARCH fanout, CLAIMS, or the scoring tail.
- N=1 single-subject path untouched (no relationship).
- Comparison-table richness beyond axes+pros/cons from claims is a follow-up.

## Verification

- TDD per slice (classification struct → branches → grounding → ordering).
- Full module + suite green.
- Fresh live run on lineage (cooperate case: Pi/Craft) must keep formal a–e AND
  fix the ordering defect (References last). A synthetic competes-case unit test
  proves the branch (no live compete task in this lineage).
- Separate-lane review before the acceptance run.

## Acceptance

Formal Part 1.3 a–e still pass on the Pi/Craft cooperate run, References is last,
and `grep -n '_INTERFACE_WORD' studio/*.py` (production) = 0 matches.
