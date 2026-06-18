# Plan — Self-Improving Bulwark (the Learning Loop)

**Status:** Proposed · **Owner:** TBD · **Target:** post-v0.1

> **Goal:** Make bulwark get *measurably better with every audit it runs.* Knowledge discovered
> in one audit (new attack patterns, validated exploits, useful invariants) should automatically
> improve every subsequent audit — without a human re-writing prompts each time.

---

## 1. The insight: the memory organ already exists, it just doesn't grow

Bulwark stages four context files into each audit. Three are per-target. The fourth —
`ATTACK_PATTERNS.md` — is deliberately different: it is **always sourced from the install dir**
and is described in the code as *"accumulated generic knowledge that shouldn't be overridden
per-project."* The multi-agent pass (Pass 2) reads it to guide variant searches.

Today that file is **frozen** — baked into the Docker image at build time. Nothing ever writes
back to it. **The learning loop is simply: make `ATTACK_PATTERNS.md` grow, durably, across runs.**

This is the cleanest possible insertion point: the agents already consume it, so we change *what's
in it*, not how the engine works.

---

## 2. The loop

```
        ┌────────────────────── audit N ──────────────────────┐
        │                                                      ▼
   (a) PULL accumulated knowledge            (c) DISTILL validated findings
       from S3, inject as ATTACK_PATTERNS         into generic, reusable patterns
        ▲          (orchestrator)                       (Claude, post-audit)
        │                                                      │
        │                                                      ▼
        └──────── (d) APPEND if novel  ◀──── dedupe vs existing knowledge base
                      (to the durable KB in S3)
```

Four components:

| # | Component | Where it lives | What it does |
|---|-----------|----------------|--------------|
| (a) | **Inject** | orchestrator (`audit_runner.py`) | Before running bulwark, fetch the accumulated KB from S3 and write it as `BULWARK_ROOT/context/ATTACK_PATTERNS.md`, so every run sees everything learned so far. |
| (b) | **Persist** | S3 (new `knowledge/` prefix) | A durable, versioned knowledge base that survives the ephemeral Fargate containers. Canonical `attack-patterns.md` + structured per-pattern JSON records. |
| (c) | **Distill** | new step (Lambda or orchestrator tail) | After the audit, take **validated** findings and ask Claude to generalise each into a reusable *pattern* (name, mechanism, detection signal, example) — protocol-agnostic. |
| (d) | **Dedupe + append** | same step | Compare distilled patterns against the existing KB; append only genuinely novel ones. |

### Worked example
A validated finding *"Yearn `TokenizedStrategy.report()` weighted-average profit-lock underflows
when new locked shares exceed previously locked shares"* gets distilled into a generic pattern:

> **AP-N: Profit-lock weighted-average underflow.** Vaults that smooth profit by locking shares
> over time can underflow/overflow the weighted-average lock calculation when a new lock exceeds
> the outstanding locked amount. **Signal:** `mulDiv`/weighted-average math over a
> `lockedShares`-style accumulator in a `report()`/`harvest()` path.

Audit #51 of an unrelated vault now carries that lesson into Pass 2 automatically.

---

## 3. The critical guardrail: only learn from validated findings

This is the make-or-break design constraint. **If unvalidated AI guesses enter the knowledge base,
every future audit inherits the hallucinations and the loop poisons itself, compounding garbage.**

**Rule:** only findings with `poc_validated = true` (i.e. they passed the Pass 3 PoC gate with a
compiling, demonstrating exploit) are eligible for distillation. Formal `VIOLATED` results may be
admitted later but are lower-trust (abstract-model artifacts are common — see the ERC20 P-6 case).

---

## 4. Phasing

- **Phase 1 — Validated-only, auto-promote.** Fully automatic. Only validated findings distil into
  the KB; novel patterns auto-append. Safe default, no human in the loop. **Recommended start.**
- **Phase 2 — Human-review queue.** Candidate patterns land in a review queue (DynamoDB +
  dashboard panel); a human approves before promotion to the canonical KB. Highest quality.
- **Phase 3 — Scoped knowledge.** Tag patterns by domain (vaults, lending, AMMs, staking) and
  inject the relevant slice per target, so a lending audit isn't diluted by AMM-specific lore.

---

## 5. Concrete build sketch (Phase 1)

1. **S3 layout** — `s3://<artefacts-bucket>/knowledge/attack-patterns.md` (canonical, injected) and
   `knowledge/patterns/<id>.json` (structured records with provenance: source job, finding id,
   distilled-at).
2. **Inject (orchestrator)** — in `_generate_context()` / staging, after writing the per-target
   files, fetch `knowledge/attack-patterns.md` and write it to `context/ATTACK_PATTERNS.md`
   (falling back to the baked-in copy if the KB is empty). Cheap, one S3 GET.
3. **Distill step** — extend the `IndexFindings` flow (or add a `DistillKnowledge` Step Functions
   state after it). Read the report's validated findings → for each, a headless Claude call
   generalises it → produce candidate pattern JSON.
4. **Dedupe + append** — embed/compare candidate patterns against existing records (title + signal
   similarity, or a cheap Claude "is this materially new?" check) → append novel ones to both the
   JSON store and the canonical markdown.
5. **Guardrail** — filter to `poc_validated == true` before distillation. Log what was admitted
   and what was dropped (never silently).

### Touch-points
- `orchestrator/src/bulwark_cloud_orchestrator/audit_runner.py` — inject step
- new `lambdas/distill_knowledge/` (or extend `lambdas/index_findings/`) — distill + append
- `infra/bulwark_cloud_infra/compute_stack.py` — wire the new state into the state machine; grant
  S3 read/write on the `knowledge/` prefix
- (Phase 2) `frontend/` + `api/` — review queue endpoints + dashboard panel

---

## 6. Open decisions

1. **Promotion policy** — validated-only/auto (Phase 1) vs human-review (Phase 2). *Recommend
   start auto.*
2. **Admit formal `VIOLATED`?** — lower trust; default **no** until we can distinguish real
   violations from abstract-model artifacts.
3. **Dedup mechanism** — embeddings vs an LLM "is-this-novel" judge. *Lean LLM judge for v1;
   simpler, no vector store.*
4. **KB growth bound** — cap size / age-out stale patterns so injected context stays focused and
   within token budget.

---

## 7. Non-goals (for now)

- Cross-tenant knowledge sharing / multi-customer isolation (single-tenant today).
- Fine-tuning or training a model — this is retrieval/context accumulation, not weight updates.
- Learning from *unvalidated* findings (explicitly excluded; see §3).

---

## 8. Success criteria

- A pattern discovered and validated in audit N is present in `ATTACK_PATTERNS.md` for audit N+1.
- The knowledge base only ever contains patterns traceable to a validated finding.
- No measurable increase in false-positive rate attributable to injected knowledge.
