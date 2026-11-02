# Test Plan: property-focus

> **Source:** `specs/property-focus/master-plan.md` (Key scenarios, Requirements and acceptance criteria, Execution order and release gates)
> **Task spec:** `specs/property-focus/tasks/task-spec.md`
> **Created:** 2026-11-02

## Scope

This is a black-box, acceptance-level checklist: what a person or an integration test should see working when each phase is done. It is grouped by phase and release gate. Each item names the plan requirement it proves ("Req" = the BTL requirement's leading words; "KS" = a Key scenarios row).

- **Layer** follows D46: `Det` = deterministic (every PR), `Replay` = replay and golden, `E2E` = browser (`make test-e2e`), `Live` = live integration.
- **Live items need explicit user approval** before anyone runs them. They use cloud credentials, a push, the real dogfood bucket, or paid provider calls. They are never automatic checks.
- Synthetic data is the default. Golden-gate metrics count only real, rights-cleared, redacted, human-reviewed cases; with none, a golden gate reports "not met", never "passed".

---

## Phase 0 — Foundation ready gate

| ID | Behavior to verify | Expected result | Traces to | Layer |
|---|---|---|---|---|
| TP-0.1 | Clean bootstrap on macOS arm64 and Linux x86_64 with Docker, no private credentials | Dependencies install from locks, services start, empty database migrates, synthetic data seeds | Req "Bootstrap the pinned monorepo…" | Det (CI) + manual log |
| TP-0.2 | Add a root `apps/`, `packages/`, `infra/` or `evals/` directory | `make check-layout` fails | Req "Bootstrap…" | Det |
| TP-0.3 | Domain code imports an adapter or runtime module; application imports REST or MCP | `check_boundaries.py` fails | Req "Bootstrap…" (D18) | Det |
| TP-0.4 | Change a manifest without updating its lock | CI fails at the frozen install step | Req "Bootstrap…" | Det (CI) |
| TP-0.5 | Build and test `backend`, `frontend` and `infrastructure/cdk` each alone | Each succeeds without building the others | Req "Bootstrap…" (D47) | Det |
| TP-0.6 | Open a PR that changes only `docs/**` or `specs/**`; open one that changes `backend/**` | Docs PR runs only secret scan and markdown lint; backend PR runs backend, generation and replay jobs | Req "Run CI path-aware…" (D48) | Live (push; approval) |
| TP-0.7 | First authenticated request from a new Cognito subject, then repeat it | One user and one owned tenant created; repeat creates nothing | Req "Isolate private data per account…" (A1) | Det |
| TP-0.8 | Account B requests account A's workflow run, and a random ID | Both return `404` with the same body | Req "Authenticate and authorize…" | Det |
| TP-0.9 | Send expired, wrong-audience, wrong-issuer and wrong-token-use tokens to REST and MCP | All rejected | Req "Authenticate and authorize…" (D28) | Det |
| TP-0.10 | Unauthenticated request to `/mcp` | `401` with `WWW-Authenticate` naming `/.well-known/oauth-protected-resource`; metadata names the Cognito issuer | Req "Authenticate and authorize…" | Det |
| TP-0.11 | Generic MCP client with no client-specific configuration | Reads challenge, discovers metadata, completes authorization code with PKCE, makes an authenticated call | KS "Generic MCP test client…"; A3 | Det |
| TP-0.12 | Search adapters, auth and configuration for client names or user agents | None found | Req "Authenticate and authorize…" (A3) | Det |
| TP-0.13 | Run one task handler through the dogfood launcher and the Lambda adapter | Identical committed results | Req "Execute work as on-demand task invocations" (D26) | Det |
| TP-0.14 | Add a looping worker command to a compose file, or a crontab line that is not `run-task <kind>` | `test_runtime_manifests.py` fails | Req "Execute work as on-demand…" | Det |
| TP-0.15 | Crash between commit and launch | Next `outbox-sweep` launches the event; exactly one downstream result | KS "Crash between commit and downstream launch"; D25, D54 | Det |
| TP-0.16 | Duplicate delivery with the same idempotency key; same key with different input | Existing result returned; `409 idempotency_conflict` | Req "Implement durable, idempotent workflow runs…" | Det |
| TP-0.17 | Lease expires; stale holder tries to complete | Recovery proceeds; stale completion rejected | KS "Concurrent task retries and expired lease" | Det |
| TP-0.18 | Unmapped failure code; flagged reconciliation code; transient failure | Test fails on unmapped code; no automatic retry for flagged code; at most four attempts with backoff | Req "Apply the D55 failure taxonomy…" | Det |
| TP-0.19 | Seed known secret values and run requests and tasks | Secrets never appear in logs; logs are JSON with trace and correlation IDs | Req "Deploy … portable secrets strategy" / "Install telemetry…" (D49, D50) | Det |
| TP-0.20 | Regenerate the OpenAPI schema and RTK Query client after a clean build | No uncommitted diff | Req "Route REST and MCP through application use cases" (D29) | Det |
| TP-0.21 | `make deploy-identity ENV=dogfood` | `cdk diff` lists only identity-stack resources; deploy succeeds | Req "Authenticate and authorize…" (phase 0) | Live (approval) |
| TP-0.22 | `make smoke-dogfood` | Synthetic user signs in through the Cognito hosted UI with PKCE; one REST call and the MCP endpoint accept the token | Req "Authenticate and authorize…" (phase 0) | Live (approval) |
| TP-0.23 | `make compatibility-local` | `docs/compatibility.md` lists each AWS operation used with pinned MiniStack digest, local result and fallback; unsupported operations listed | Req "Establish real AWS compatibility…" (D19) | Det |

**Gate pass:** TP-0.1–0.23 green and recorded in `specs/property-focus/gates/foundation-ready.md`, plus the Cognito dynamic-registration research result (CP-0.A).

---

## Phase 1 — Intake ready gate

| ID | Behavior to verify | Expected result | Traces to | Layer |
|---|---|---|---|---|
| TP-1.1 | Two `imap-poll` invocations start in the same minute | One polls; the other exits without fetching | KS; Req "Poll the MLS mailbox…" (D22) | Det |
| TP-1.2 | One `imap-poll` invocation | Polls every `IMAP_POLL_INTERVAL_SECONDS` (default 20; values outside 15–30 rejected), starts no poll after 45 s, exits before 60 s; poll gap is measured, not asserted ≤30 s | Req "Poll the MLS mailbox…" | Det |
| TP-1.3 | `imap-poll` receives a message | Raw MIME and digest stored as evidence, intake row and outbox event committed; no parsing in the intake task; zero model-provider calls | Req "Preserve and deduplicate MLS intake…" | Det |
| TP-1.4 | Same email delivered three times under two UIDs; replay of parsed content | Delivery history kept; one source observation per content digest; no duplicate listing episode | KS; Req "Preserve and deduplicate…" | Det |
| TP-1.5 | UIDVALIDITY reset; malformed message; attachment bomb; database down during intake | Safe rescan; malformed and bomb messages quarantined with evidence and visible; mailbox keeps going; checkpoint not advanced without durable intake | Req "Preserve and deduplicate…"; error table | Det |
| TP-1.6 | Store identical evidence bytes twice; try to overwrite a committed object | One object at `sha256/<first2>/<hex>`, two refs; overwrite fails; re-fetch verifies SHA-256 | Req "Implement content-addressed evidence integrity…" (D44) | Det |
| TP-1.7 | Two condos in one building offered separately | Two properties, two feed entries | KS; Req "Resolve property and listing identities…" (D4) | Det |
| TP-1.8 | Whole duplex offered once | One property and one feed entry with two unit records | KS | Det |
| TP-1.9 | Relisting with a new external ID | New listing, same property, history kept | KS | Det |
| TP-1.10 | Ambiguous address-only match; merge then split by a reviewer | Enters identity review; merge and split keep aliases and evidence links with an audit trail naming actor and reason; non-role caller gets `403` | Req "Resolve property and listing identities…" | Det |
| TP-1.11 | Price change on an existing listing; fault before commit | Listing state, history, domain event and outbox event written in one transaction; fault leaves none | KS; Req "Commit listing state changes…" (D25) | Det |
| TP-1.12 | Older price/status observation arrives after a newer one | Newer status kept; both observations retained | Error table | Det |
| TP-1.13 | `POST /properties` with an allowlisted URL, an address of an existing property, a non-allowlisted URL | `202 {property_id, workflow_run_id}` with a `MANUAL` observation; links to existing property; `422 unsupported_source` | Req "Allow manual property addition…" (D17) | Det |
| TP-1.14 | Phase 1 MCP tools (`list_properties`, `get_property`, `get_property_listings`, `get_property_history`, `add_property`, `get_workflow_run`) versus REST | Equivalent canonical results for the same principal | Req "Route REST and MCP…" | Det |
| TP-1.15 | Replay the retained email corpus | No duplicate listing episode or observation | Phase 1 exit evidence | Replay |
| TP-1.16 | Add a synthetic case to `fixtures/golden/` | Manifest load fails | KS; Req "Evaluate changes…" (D46) | Replay |
| TP-1.17 | `PF_LIVE=1 make test-live` mailbox check | Authorized mailbox reachable through the secret provider | Test strategy, Live layer | Live (approval) |

**Gate pass:** TP-1.1–1.16 green; replay report, raw MIME integrity checks, poll-overlap test, ambiguity review fixture and phase 1 MCP parity recorded in `intake-ready.md`.

---

## Phases 2–3 — Intelligence ready gate

### Phase 2: TheMLS enrichment

| ID | Behavior to verify | Expected result | Traces to | Layer |
|---|---|---|---|---|
| TP-2.1 | Request a capability with no registered provider | Component `DEFERRED` with `DATA_UNAVAILABLE`; no error | Req "Capture TheMLS pages…" (D11) | Det |
| TP-2.2 | Capability configured with a shadow provider | Shadow output recorded as a `SHADOW` analysis run; publishes nothing | Req "Capture TheMLS pages…" | Det |
| TP-2.3 | Capture the recorded listing page | Normalized facts, source time, HTML and media stored with stable evidence locators; `BROWSER` observation, no direct property insert | Req "Capture TheMLS pages…"; Req "Model every source…" | Det |
| TP-2.4 | Session-expired page | Source account `AUTH_REQUIRED`, workflow `WAITING_FOR_USER` after one detection, one operator action; resumes after operator refresh | Req "Capture TheMLS pages…" (D23) | Det |
| TP-2.5 | Two tasks for one source account at once; stale lease holder writes | Tasks serialize; stale holder cannot overwrite newer session or result | Req "Capture TheMLS pages…" | Det |
| TP-2.6 | Disallowed host, private-IP redirect, oversized download, unsupported media | Each fails with an explicit code; no internal network fetch | Req "Capture TheMLS pages…" | Det |
| TP-2.7 | Account A requests account B's private evidence ID | Indistinguishable `404`; no signed URL | KS | Det |
| TP-2.8 | Signed evidence download | Expires (default 5 minutes); entitlement checked at issuance | Req "Implement content-addressed evidence integrity…" | Det |
| TP-2.9 | Source session drill: capture, expire, resume, recapture | Evidence digests unchanged; nothing overwritten | Phase 2 exit evidence | Replay |

### Phase 3: Property intelligence

| ID | Behavior to verify | Expected result | Traces to | Layer |
|---|---|---|---|---|
| TP-3.1 | Load ontology seed with 74 attributes, a missing category, a non-scalar rule parameter, or an unregistered rule | `ontology load` fails | KS; Req "Store the ontology in Postgres…" (D32, D33) | Det |
| TP-3.2 | Load and publish v1.0.0; edit a published version | `DRAFT` then `ACTIVE`, prior `RETIRED` in one transaction; edit fails; 75+ attributes in 12 categories | Req "Store the ontology…" | Det |
| TP-3.3 | Contradictory bedroom claims | Deterministic resolution or `CONFLICTING`, with both evidence links | KS; Req "Resolve claims…" (D7, D12) | Det |
| TP-3.4 | Missing attribute | `UNKNOWN`; never treated as false | Req "Resolve claims…" | Det |
| TP-3.5 | Human correction of kitchen quality | `HUMAN` claim wins; analyzer claim and run remain | KS (D36) | Det |
| TP-3.6 | Human claim contradicts assessor parcel ID without evidence | Assessor value stays resolved | KS | Det |
| TP-3.7 | Insert an `ANALYZER` claim without evidence | Database constraint rejects it | Req "Implement content-addressed evidence integrity…" (D35) | Det |
| TP-3.8 | Replay identical inputs and versions | Same profile output digest | Req "Resolve claims…" | Det |
| TP-3.9 | Profile built from evidence restricted to account A, read by account B | Not served | Req "Keep reusable intelligence separate…" | Det |
| TP-3.10 | Source price change; `LAZY` attribute read; new ontology version published; `MANUAL` attribute | `EVENT` recomputes dependents only; `LAZY` recomputes on read; `BATCH` invalidations and no analysis run until the sweep; `MANUAL` waits for reanalyze | KS "New ontology version published"; Req "Track invalidation…" (D13) | Det |
| TP-3.11 | Stale value | Shows invalidation time; status unchanged | Req "Track invalidation…" | Det |
| TP-3.12 | Create a search subscription; patch geography without and with `confirm_material`; stale `If-Match` | Owner membership row created; `409 material_change_requires_confirmation`; then `200` new version recording the member; `412` | KS; Req "Version search subscriptions…" (D16, D17) | Det |
| TP-3.13 | Revert a subscription to an earlier version | New `REVERT` version with identical content digest | Req "Version search subscriptions…" | Det |
| TP-3.14 | Non-member calls every search-scoped route and MCP tool (reads and writes) | `404` with the same body as a nonexistent search | Req "Isolate private data per account…" (A1) | Det |
| TP-3.15 | Property fails every subscription's known hard requirements | No `BASELINE` run, zero budget reservations, zero provider calls | KS; Req "Apply a deterministic hard pre-filter…" (D3) | Det |
| TP-3.16 | New subscription accepts a property that every earlier subscription rejected | Pre-filter re-run makes it survive; exactly one `BASELINE` run | KS | Det |
| TP-3.17 | Pre-filter runs with no LLM; candidate stage in shadow | Passes offline; version recorded on each decision; shadow disagreements reported without changing outcomes | Req "Apply a deterministic hard pre-filter…" | Det |
| TP-3.18 | Missing hard requirement with high soft score | Requirements `unknown`; listed after all `pass` homes; `fail` hidden unless filtered | KS; Req "Implement deterministic evaluation…" (D9) | Det |
| TP-3.19 | Property appears before analyzers finish | Listed with listing facts and requirements; tier states only `NOT_TRIGGERED`, `PENDING`, `PARTIAL`, `COMPLETE` | Req "Run two analysis tiers…" (D53) | Det |
| TP-3.20 | One optional enrichment fails | `PARTIAL` profile, successful facts kept, component retry information | KS | Det |
| TP-3.21 | Member requests deep analysis | `MANUAL` trigger, `DEEP` run charged to that member, property usable throughout | KS; Req "Run two analysis tiers…" (D1, D6) | Det |
| TP-3.22 | Deep tier needs `ASSESSOR`, `PERMITS`, `ZONING`, `TAX_HISTORY` | Components `DEFERRED`; attributes `UNKNOWN` | Risks (public-record provider) | Det |
| TP-3.23 | Remap `VISION` (and each other capability) from OpenAI to Anthropic in configuration | Next run records the other provider; no analyzer code changed | KS; Req "Implement specialized, versioned analyzers…" (A2) | Det |
| TP-3.24 | Shared provider contract with recorded responses, no network | Both OpenAI and Anthropic adapters pass | Req "Implement bounded, provider-independent AI calls…" (A2) | Det |
| TP-3.25 | Capability no provider offers | `unsupported_capability` before any paid call | Req "Implement specialized…" | Det |
| TP-3.26 | Invalid model output; LLM output invents an ontology key | One bounded repair, then `schema_invalid`; no published unsupported claim | KS; Req "Implement bounded…" | Det |
| TP-3.27 | Prompt injection in source HTML or email | No tool execution, schema change, credential access or write | KS | Det |
| TP-3.28 | Identical computation key; one changed version | Existing run reused; new run created; no run overwritten | Req "Implement specialized…" (D54, D8) | Det |
| TP-3.29 | Two paid calls compete for remaining tier funds | At most the budget-supported reservation succeeds | KS; Req "Enforce cost reservations…" (D56) | Det |
| TP-3.30 | Budget exhausted | Optional calls not made; components `DEFERRED`; intake and existing results usable | Req "Enforce cost reservations…" | Det |
| TP-3.31 | Provider succeeds then the task crashes before step completion | One committed result; reservation reconciled by `repair`; no blind re-call | Req "Implement durable, idempotent workflow runs…"; error table `provider_outcome_unknown` | Det |
| TP-3.32 | Cancellation wins the commit race | No new profile published; committed evidence and usage kept | Req "Implement durable, idempotent workflow runs…" | Det |
| TP-3.33 | `GET /verifications` | VOI-ordered; below-threshold and unreferenced attributes never asked | Req "Prioritize human verification…" (D37) | Det |
| TP-3.34 | Shadow candidate scores better | Report only; no profile, evaluation or notification change | KS; Req "Evaluate changes…" (D45) | Replay |
| TP-3.35 | Harness `EXPERIMENT` trigger on a rejected property | Run records analysis tier and budget tier `EXPERIMENT`; reserves `EXPERIMENT` budget; no notification | Req "Run two analysis tiers…" | Replay |
| TP-3.36 | Evaluation report | Includes identity precision, field correctness/abstention, hard-filter false passes, evidence support, trigger rate, cost and latency per version; golden gate "not met" with no real cases | Req "Evaluate changes…" (D46) | Replay |
| TP-3.37 | Phase 2–3 MCP tools versus REST | Equivalent canonical results; same membership checks | Req "Route REST and MCP…" | Det |
| TP-3.38 | `PF_LIVE=1 make test-live` provider checks | Both providers respond under the configured budget | Test strategy, Live layer | Live (approval, after CP-3.A) |

**Gate pass:** phase 2 and 3 items green; artifacts in `intelligence-ready.md`.

---

## Phase 4 — Evaluation ready gate

| ID | Behavior to verify | Expected result | Traces to | Layer |
|---|---|---|---|---|
| TP-4.1 | Create a second `ACTIVE` profile for one account | Rejected | Req "Separate the experience…" (A1) | Det |
| TP-4.2 | Patch a profile with a material change without and with `confirm_material`; stale `If-Match` | `409`; then `200` new version; `412` | Req "Version the PreferenceProfile…" (D16) | Det |
| TP-4.3 | Revert to an earlier preference version | New version with identical content digest | KS | Det |
| TP-4.4 | Reference an unknown preference definition | `422` | Req "Version the PreferenceProfile…" (D34) | Det |
| TP-4.5 | Learned weight change of 0.03 share | Auto-applied `LEARNED` version with evidence | KS | Det |
| TP-4.6 | Learned change to a profile hard requirement | `PROFILE` proposal awaiting approval; profile version unchanged until approval | KS | Det |
| TP-4.7 | Learned or interview change to a subscription's budget or geography | `SUBSCRIPTION` proposal awaiting approval; subscription and profile unchanged | KS | Det |
| TP-4.8 | Member B approves a material subscription proposal from member A's feedback; second decision | `200`, new version, `decided_by` B; second decision `409`; B sees count of A's feedback, not its content | KS; Req "Version the PreferenceProfile…" | Det |
| TP-4.9 | Another account decides a `PROFILE` proposal, or reads B's profile, versions, proposals or interview sessions | `404`, including for members of B's searches | Req "Isolate private data per account…" | Det |
| TP-4.10 | Apply a profile with a `household_lifestyle` commute requirement and a `property` bedroom requirement to a rental hold search and an owner-occupancy search | Rental hold copies only the bedroom requirement (home `pass`); owner-occupancy copies both (home `fail`) | KS | Det |
| TP-4.11 | Edit a private profile after applying it to a shared search | No new search version; evaluations unchanged | KS | Det |
| TP-4.12 | Interview structured slots; geography answer | Slots (geography, budget, bedrooms, property type, deal-breakers) first; geography becomes a `SUBSCRIPTION` proposal, never a profile field | Req "Run the adaptive preference interview…" (D15) | Det |
| TP-4.13 | Interview turn 20 | Model request carries state plus last 6 turns; payload no larger than turn 7 plus state delta | KS | Det |
| TP-4.14 | Interview session for a search the caller does not belong to | `404` | Req "Run the adaptive preference interview…" | Det |
| TP-4.15 | Fixed profile revision, subscription version and scoring version | Identical requirements, dimension scores, coverage, critical mismatches, overall and explanation-input digest | Req "Implement deterministic evaluation…" (D9) | Det |
| TP-4.16 | Unknown values in a dimension | Contribute zero against the fixed denominator; coverage drops | Req "Implement deterministic evaluation…" | Det |
| TP-4.17 | Explanation model output contains a different score | Stored numbers unchanged | KS | Det |
| TP-4.18 | Preference or search change affecting only weights | New evaluation version; no source recapture or analyzer rerun | KS; Req "Keep reusable intelligence separate…" | Det |
| TP-4.19 | Property in the subscription's top quartile; price drop of 5%; `CONFLICTING` hard attribute | `AUTO` trigger with `deep_trigger_v1` and reasons | KS; Req "Run two analysis tiers…" (D6) | Det |
| TP-4.20 | Golden replay trigger rate | Non-anomaly `AUTO` triggers fire for 20–30% of baseline-evaluated properties; rate in the report (not met without real cases) | Req "Run two analysis tiers…" | Replay |
| TP-4.21 | Phase 4 MCP tools (`get_preferences`, `update_preferences`) versus REST | Equivalent results; material change needs `confirm_material` | Req "Route REST and MCP…" | Det |

**Gate pass:** items green; user review of ontology and preference definitions recorded (CP-4.B); artifacts in `evaluation-ready.md`.

---

## Phase 5 — Dogfood ready gate

| ID | Behavior to verify | Expected result | Traces to | Layer |
|---|---|---|---|---|
| TP-5.1 | Shortlist the same home twice in subscription 1, then open subscription 2 | `201` then `200`, one entry; a second member sees it with `saved_by`; subscription 2 empty; non-member `404` | KS; Req "Let subscription members shortlist…" | Det |
| TP-5.2 | Flag a visit question in subscription 1; empty text | Listed for subscription 1 only, visible to every member; non-member `404`; empty text `422` | KS | Det |
| TP-5.3 | Member A writes a note and a visit question | Member B sees the visit question, not the note | KS; Req "Isolate private data per account…" | Det + E2E |
| TP-5.4 | Record a tour | Tour visible to members; at most three post-tour verification requests for the recorder, highest VOI first | KS (D37) | Det |
| TP-5.5 | Owner invites `spouse@example.com`; spouse signs up with that verified email and accepts | `201`, one email; accept `200`; spouse lists the search with role `member` | KS; Req "Isolate private data…" (A1) | Det + E2E |
| TP-5.6 | Repeat invite for a pending email; member invites; non-member invites | `200` and no email; `403`; `404` | Req "Isolate private data…" | Det |
| TP-5.7 | Different verified email, expired token, used token, unknown token | Each `404`; no membership row | KS | Det |
| TP-5.8 | Owner removes the spouse, who then reads the shortlist and calls `get_property_evaluation` | Both `404` on the first request after removal; no further notifications | KS | Det + E2E |
| TP-5.9 | Remove the owner; a member removes another member | `409 owner_cannot_leave`; `403` | Req "Isolate private data…" | Det |
| TP-5.10 | Search event on a search with two members; replay the event | One notification per member through each member's channel settings; replay creates no duplicate | KS; Req "Provide email and in-app notifications…" (D51) | Det |
| TP-5.11 | Email send times out with unknown outcome | `notification_unknown`; no automatic retry; `repair` reconciles before any resend | KS | Det |
| TP-5.12 | Register a fake notification strategy | Works with no change to selection logic | Req "Provide email and in-app notifications…" | Det |
| TP-5.13 | Notification content and deep link | Links to `/homebuyer/properties/{id}`; no private financial details; link requires sign-in and authorization | Req "Provide email and in-app…"; Req "Deliver the phase 5 user loop…" (D10) | Det + E2E |
| TP-5.14 | Full user loop: sign in, interview, feed, evidence, fit dimensions and unknowns, edit preferences, approve a proposal, submit a correction, add a property by address, shortlist and flag a visit question | Completes in the browser | Req "Deliver the phase 5 user loop…" | E2E (`dogfood.spec.ts`) |
| TP-5.15 | Property page | Seven headings: Summary, Photos, Personal Fit, Property Intelligence, Deep Research, Timeline, Feedback | Req "Deliver the phase 5 user loop…" (D10) | E2E |
| TP-5.16 | Location or enrichment timeout | Property facts visible with component status, last success time and retry information; no color-only status | Req "Deliver the phase 5 user loop…" (D53) | E2E |
| TP-5.17 | Accessibility | Zero serious or critical axe violations on feed, property dialogs and verification; keyboard-only path works | Req "Deliver the phase 5 user loop…" | E2E (`accessibility.spec.ts`) |
| TP-5.18 | Operations screen and API as operator and as non-operator | Operator sees failed work by class, credential state, costs (unknown never shown as zero), audited retry and budget changes; non-operator gets `403` and no controls | Req "Apply the D55 failure taxonomy…"; Req "Enforce cost reservations…" (phase 5) | Det + E2E |
| TP-5.19 | Fixture email replay | One linked trace from intake through property, workflow, analysis run, model call, claim and notification | KS; Req "Install telemetry…" (D50) | Det |
| TP-5.20 | Backup freshness | `backup` task fails when the latest backup is older than 26 hours; retention 30 daily, 12 monthly | Req "Install telemetry, backup…" (D52) | Det |
| TP-5.21 | `make restore-check`; production target | Restores into isolation and verifies counts, digests and decryption; refuses production | Test strategy | Det |
| TP-5.22 | Dogfood bucket: overwrite and delete a committed object with runtime credentials, then restore from backup | Recorded version and SHA-256 unchanged; restored objects match digests | KS; Req "Implement content-addressed evidence integrity…" | Live (approval) |
| TP-5.23 | Two accounts sharing one search complete intake-to-review on DigitalOcean; restore drill | Loop completes; removed member gets `404`; measured RPO/RTO recorded against 24 h / 8 h | Phase 5 exit; KS "Backup restore after host loss" | Live (approval) |
| TP-5.24 | `record_feedback` MCP tool versus REST | Equivalent results and privacy | Req "Route REST and MCP…" | Det |

**Gate pass:** items green; user-confirmed preference version and CP-5.A recorded; artifacts in `dogfood-ready.md`.

---

## Phase 6 — Location ready gate

| ID | Behavior to verify | Expected result | Traces to | Layer |
|---|---|---|---|---|
| TP-6.1 | Location profile for a property | Traffic keys present; measured values `OBSERVED`, estimates `INFERRED` | Req "Deliver location intelligence…" (D42) | Det |
| TP-6.2 | `location.traffic_level` as a hard requirement and as a soft preference | Works in both; unknown stays unknown | Req "Deliver location intelligence…" | Det |
| TP-6.3 | A fellow search member requests another member's profile destination or its travel times | `404` | Req "Deliver location intelligence…" (D43, A1) | Det |
| TP-6.4 | Change a profile destination; change a search destination | Only the owner's travel times recompute and no evaluation changes; the search's travel times and evaluations recompute | Req "Deliver location intelligence…" | Det |
| TP-6.5 | Rental hold search evaluates a home far from a member's work destination | `lifestyle_fit` null; travel time does not affect overall | KS | Det |
| TP-6.6 | Apply a profile whose preferences reference profile destinations | `409` lists each destination that would become visible | Req "Version search subscriptions…" | Det |
| TP-6.7 | Shared search property page | Each member sees the shared evaluation plus their own private travel times only | ATL Reusable intelligence (plan recommendation) | E2E |
| TP-6.8 | Field-level location evaluation | Report split by measured versus inferred | Phase 6 exit evidence | Replay |

**Gate pass:** items green; source-rights entry recorded; artifacts in `location-ready.md`.

---

## Phase 7 — Valuation ready gate

| ID | Behavior to verify | Expected result | Traces to | Layer |
|---|---|---|---|---|
| TP-7.1 | Valuation for a property with enough comps | p50, ordered p10/p90, confidence label and width ratio, list-price comparison, comps each with per-feature similarity, weight and reason, through `GET /properties/{id}/intelligence` | Req "Gate probabilistic valuation…" (D39, D40) | Det |
| TP-7.2 | Sparse comps; future-dated sale | `insufficient_data` abstention; future sale excluded from runs and backtests | KS | Det |
| TP-7.3 | Two valuations with equal p50 above list and different widths | Narrower interval gives the higher value score | KS (D40) | Det |
| TP-7.4 | Owner-occupancy evaluation after phase 7 | `scoring_v2` weights lifestyle 0.5, quality 0.25, value 0.25 | Req "Implement deterministic evaluation…" | Det |
| TP-7.5 | List price below valuation p10 | `AUTO` anomaly trigger | Req "Run two analysis tiers…" | Det |
| TP-7.6 | Backtest on time-separated holdout | MdAPE, bias and p10–p90 coverage reported, including sparse segments; promotion refused without thresholds frozen in `evaluation-policy.md` | Req "Gate probabilistic valuation…" | Replay |

**Gate pass:** frozen thresholds, holdout report, comp explanation audit and abstention behavior in `valuation-ready.md`.

---

## Phase 8 — Investor and offer ready gate

| ID | Behavior to verify | Expected result | Traces to | Layer |
|---|---|---|---|---|
| TP-8.1 | Upload a rent roll, lease or expense statement; repeat the digest; oversize; wrong type | `202` with private evidence; existing evidence returned; `413`; `415` | Req "Gate probabilistic valuation and tenant offer strategy…" (phase 8) | Det |
| TP-8.2 | Rent roll unit labels | Dated unit claims; collected and projected rent distinct; no new sale listing | Req (phase 8 documents); Req "Resolve property and listing identities…" | Det |
| TP-8.3 | Duplex A/B rent $1,500/$1,200, zero vacancy, $700 expenses, $800 debt service, $100 reserves | Rental hold $1,100/month; occupying A gives −$400/month; both on the same valuation revision | KS | Det |
| TP-8.4 | Missing rent for B, only an agent projection | Contractual-basis cash flow unknown naming B; projection scenario labels its assumption | KS | Det |
| TP-8.5 | Live-in search requires a unit available at closing; no supporting claim | Requirements `unknown`; home in the `unknown` group, not among confirmed matches | KS | Det |
| TP-8.6 | Rental hold soft criteria: cash flow floor $0 target $1,000 (w 1), DSCR floor 1.00 target 1.25 (w 1); duplex cash flow $500, DSCR 1.10 | `u` 0.5 and 0.4; investment 45, coverage 1.0; `scoring_v3` weights investment 0.5, value 0.3, quality 0.2 | KS | Det |
| TP-8.7 | Add cap-rate floor 4% target 6% (w 1); debt service unknown; cap rate 5% | Investment ≈16.7, coverage 1/3; requirements unaffected | KS | Det |
| TP-8.8 | Investment criterion with `floor >= target` | `422` | Req "Implement deterministic evaluation…" | Det |
| TP-8.9 | Investor feed with two `pass` homes at `scoring_v3` overall 72 and 58 | 72 first; ties break by `id` | KS | Det + E2E |
| TP-8.10 | Offer scenario with hard limit below p50 | All four outputs at or below the limit; limit listed as binding; `value ≤ competitive ≤ win-focused ≤ walk-away` | KS; Req (offer scenarios) (D41) | Det |
| TP-8.11 | Recalculate a scenario from identical frozen inputs | Identical outputs | Req (offer scenarios) | Det |
| TP-8.12 | Another account, including a fellow search member, reads a scenario or outcome | `404` | Req "Isolate private data per account…" | Det |
| TP-8.13 | Scenario output | No seller acceptance probability; expense lines labelled `claim` or `buyer_assumption`; missing inputs listed; available subtotals shown | Req (offer scenarios) | Det |
| TP-8.14 | One account, two subscriptions with distinct strategies; edit one; switch a duplex between strategies | Other subscription's evaluations unchanged; duplex evaluated under both | Req "Separate the experience…"; Phase 8 exit | E2E (`products.spec.ts`) |
| TP-8.15 | Homebuyer property page for a duplex | Live-in scenario available | Phase 8 scope | E2E |
| TP-8.16 | Phase 8 MCP tools (`create_offer_scenario`, `get_offer_scenario`) versus REST | Equivalent results and privacy | Req "Route REST and MCP…" | Det |

**Gate pass:** items green; artifacts in `investor-offer-ready.md`; launch size confirmed (CP-8.B).

---

## Phase 9 — AWS ready gate

| ID | Behavior to verify | Expected result | Traces to | Layer |
|---|---|---|---|---|
| TP-9.1 | CDK security assertions | No public access; scoped IAM; encryption; S3 versioning; deny-overwrite/delete for runtime roles; Object Lock only on permitted classes | Req "Implement content-addressed evidence integrity…" | Det |
| TP-9.2 | Workflow assertions | `analyze.asl.json` has 5–15 states with retries, catches and timeouts; reference-only payloads; no Temporal dependency in any manifest | Req "Establish real AWS compatibility…" (D21) | Det |
| TP-9.3 | `make smoke-aws` with a non-allowlisted environment | Refuses | Test strategy | Det |
| TP-9.4 | Staging deployment smoke | S3, SQS redelivery and DLQ, EventBridge `imap-poll` schedule, Step Functions retries and catches, Lambda auth on REST and MCP, Fargate browser launch all pass | Req "Establish real AWS compatibility…" | Live (approval) |
| TP-9.5 | `docs/compatibility.md` after staging | Every operation has local and AWS results; no emulator-only pass counted | Req "Establish real AWS compatibility…" (D19) | Live (approval) |
| TP-9.6 | AWS-side shadow evaluation | Runs with no publication | Req "Evaluate changes…" (D45) | Live (approval) |
| TP-9.7 | Fault injection and budget alarms | Failures land in classified failed work; alarms fire | Phase 9 exit | Live (approval) |
| TP-9.8 | Point-in-time restore and rollback drill | Database, evidence references, encryption access and configuration restored; image rollback without reversing destructive schema | Req "Install telemetry, backup and recovery controls" / "Deploy through GitHub Actions…" (D52) | Live (approval) |
| TP-9.9 | `pnpm --filter @property-focus/infra diff --context environment=staging` | Reviewed diff recorded | AWS ready artifact | Live (approval) |

**Gate pass:** CDK diff review, AWS smoke, failure/recovery and rollback reports in `aws-ready.md`.
