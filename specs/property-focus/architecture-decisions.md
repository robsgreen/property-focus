# Architecture Decision Register — Property Intelligence Platform

**Status:** Confirmed  
**Purpose:** Companion document to the master plan. This document enumerates the 56 architecture decisions made during planning, plus amendments A1–A3 confirmed by the user on 2026-11-02, so implementation and task-generation agents do not need the original conversation.

---

## 1. Personal real-estate analyst + conditional deep diligence

**Decision**

The product is primarily a personal real-estate analyst for every qualifying property, with deeper buyer-intelligence / diligence analysis triggered only for promising, anomalous, or manually selected properties.

**Rationale**

The system should do more than discover listings. Its primary value is interpreting each property for the buyer: why it fits, why it does not, what is concerning, and whether it is worth seeing. Full diligence on every property would be unnecessarily expensive and slow.

**Key implications / constraints**

- Every surviving property receives a baseline “B-level” analysis.
- “C-level” deep research is conditional.
- Deep research must also be manually triggerable.
- The system should remain useful even before deep diligence completes.

---

## 2. Multi-source property ingestion from day one

**Decision**

Design ingestion around a `PropertySource` abstraction so MLS email is the initial discovery source, but Zillow, Redfin, Realtor, RESO, or future sources can be added without changing the property domain.

**Rationale**

MLS email is the practical V1 entry point, but source lock-in would create unnecessary architectural debt.

**Key implications / constraints**

- Sources emit source observations/events, not canonical properties.
- Normalization and identity resolution happen after ingestion.
- Field-level provenance and disagreements must be retained.
- TheMLS email/browser is the first concrete implementation, not the permanent architecture.

---

## 3. Deterministic hard filtering before AI ranking

**Decision**

Apply deterministic hard filters before expensive enrichment or AI analysis. AI ranks surviving properties; it does not silently override hard constraints.

**Rationale**

Explicit buyer constraints are cheaper, clearer, and safer to evaluate deterministically.

**Key implications / constraints**

- Hard rules may reject a property.
- AI may rank, summarize, explain, and identify tradeoffs.
- Funnel stages should be pluggable and shadow-testable.
- Filtering logic must be testable without an LLM.

---

## 4. Canonical identity is the physical property / parcel

**Decision**

The durable `Property` entity represents the physical property or parcel. A `Listing` is a source-specific marketing/sale event attached to that property.

**Rationale**

The same house can be relisted, repriced, withdrawn, sold, and listed again. Treating each listing as the property would fragment history.

**Key implications / constraints**

Identity resolution should prefer:

1. APN / parcel identifier
2. normalized address + coordinates
3. source-specific IDs

Identity resolution must carry confidence and preserve ambiguous cases.

---

## 5. Hybrid preference model with tenant-scoped profiles

**Amended 2026-11-02 (user):** superseded in part by A1 below.

**Decision**

Represent buyer preferences using explicit structured preferences plus examples and feedback. Support multiple tenants from the start, with one shared active household profile in V1.

**Rationale**

Preferences include hard requirements, weighted preferences, soft preferences, anti-preferences, and learned examples. A multi-user household may share one buying profile initially, but tenant boundaries must exist from the first migration.

**Key implications / constraints**

- Separate global property facts from tenant-private preferences and evaluations.
- Support `Tenant`, `User`, `PreferenceProfile`, `Search`, feedback, tours, offers, etc.
- Tenant isolation is required from day one.
- Avoid premature billing/org/RBAC complexity.

---

## 6. Moderately aggressive conditional deep research

**Decision**

Automatically deep-research roughly the top 20–30% of properties, plus anomaly/opportunity cases, while allowing manual deep-analysis requests.

**Rationale**

Deep research is valuable but more expensive. A moderate trigger rate balances usefulness with cost.

**Key implications / constraints**

Deep-analysis triggers should record:

- `AUTO`
- `MANUAL`
- `EXPERIMENT`

Also store the trigger strategy and reasons.

Rejected properties may still be analyzed for evaluation purposes.

---

## 7. Deep property analysis is user-independent

**Decision**

Research and understand a property once, then apply tenant preferences afterward.

**Rationale**

Expensive property understanding can be reused across households. Preference changes should not require re-running vision, permits, assessor, or location research.

**Key implications / constraints**

Pipeline shape:

`Sources → Normalize/Identity → Property Intelligence → Tenant Preference Matching`

Every inferred attribute should record:

- value
- confidence
- status (`OBSERVED`, `INFERRED`, `UNKNOWN`, `CONFLICTING`)
- evidence
- analyzer version

Unknown must never be treated as false.

---

## 8. Preserve historical and versioned intelligence

**Decision**

Preserve prior listing facts, photos, remarks, prices, provider outputs, and analysis runs instead of overwriting them.

**Rationale**

The system should be auditable and replayable. Historical data is also useful for evaluating analyzer/model changes.

**Key implications / constraints**

- Never overwrite old analysis runs.
- Track freshness and invalidation.
- Distinguish listing-specific, property-specific, and market-specific intelligence.
- This does **not** require pure event sourcing; Decision #25 refines the implementation approach.

---

## 9. Multidimensional fit output

**Decision**

Fit must be multidimensional rather than a single opaque score.

**Rationale**

A property can be excellent in one dimension and unacceptable in another. Buyers need to see why a recommendation exists.

**Key implications / constraints**

Example dimensions:

- Overall fit
- Requirements
- Lifestyle fit
- Property quality
- Value
- Critical mismatches

Deterministic logic computes scores/penalties/unknown handling. LLMs may explain but should not invent hidden numeric scores.

---

## 10. Notifications plus dedicated web UI

**Decision**

Notifications alert the buyer, but the web application is the canonical place to inspect the property and analysis.

**Rationale**

Notifications are transient. The property page should hold the complete history, evidence, fit, and research.

**Key implications / constraints**

Property page sections should include:

- Summary
- Photos
- Personal Fit
- Property Intelligence
- Deep Research
- Timeline
- Feedback

Notifications deep-link into the property page.

---

## 11. Capability-based enrichment

**Decision**

Enrichers advertise capabilities rather than being hard-coded by vendor.

**Rationale**

The same capability may come from multiple providers, and providers may change over time.

**Key implications / constraints**

Example capabilities:

- `SALE_HISTORY`
- `TAX_HISTORY`
- `ASSESSOR`
- `AVM`
- `OWNERSHIP`
- `PERMITS`
- `ZONING`
- `PLANNING`
- `STREET_IMAGERY`
- `LISTING`
- `PHOTOS`
- `REMARKS`
- `LISTING_HISTORY`

Use primary, secondary, and shadow providers where useful.

---

## 12. Immutable claims plus attribute-specific resolution

**Decision**

Store provider/analyzer outputs as immutable claims, then separately resolve the current best value for each attribute.

**Rationale**

Providers will disagree. Losing values are still useful evidence and should not be discarded.

**Key implications / constraints**

A claim should include:

- property
- attribute
- value
- provider
- source type
- observed time
- confidence
- evidence

Resolution rules may differ by attribute. For example, list price should prefer the current listing, while parcel ID should prefer assessor data.

---

## 13. Separate invalidation from recomputation

**Decision**

Track which capabilities/findings become stale independently from the mechanism that recomputes them.

**Rationale**

Not every change requires immediate reanalysis, and different data has different freshness characteristics.

**Key implications / constraints**

Support invalidation modes:

- `EVENT`
- `LAZY`
- `BATCH`
- `MANUAL`

Ontology changes may mark results eligible for recomputation without forcing an immediate backfill.

---

## 14. Modular AI analysis, not one giant prompt

**Decision**

Use specialized evidence extractors and analyzers rather than one giant multimodal property prompt.

**Rationale**

Specialized analyzers are easier to test, version, benchmark, and replace.

**Key implications / constraints**

Examples:

- PhotoAnalyzer
- FloorplanAnalyzer
- TextAnalyzer
- LayoutAnalyzer
- IndoorOutdoorAnalyzer
- ConditionAnalyzer
- KitchenAnalyzer
- YardAnalyzer
- GarageAnalyzer
- NaturalLightAnalyzer
- PrivacyAnalyzer
- GuestSuiteAnalyzer

Reuse cached evidence. Record model, prompt/analyzer version, cost, latency, tokens, and confidence.

---

## 15. Adaptive preference interview

**Decision**

Use an adaptive interview that combines structured questions, conversation, examples, and property feedback.

**Rationale**

The underlying lifestyle goal often matters more than a literal requested feature.

**Key implications / constraints**

Preference model should connect:

`Lifestyle Goal → Preference / Requirement → Satisfying Attributes → Importance / Tradeoffs → Examples`

Maintain explicit interview state rather than repeatedly sending a giant transcript.

Voice may be added as an interaction adapter.

---

## 16. Preference evolution is versioned and selectively automatic

**Decision**

Low-impact preference updates may be learned automatically, while material changes require explicit approval.

**Rationale**

The system should learn without silently changing major purchase criteria.

**Key implications / constraints**

Approval is required for changes such as:

- hard requirements
- major weights
- budget
- geography
- major tradeoffs

All preference changes should be reversible and evidence-backed.

---

## 17. Property-centric web app with searches as subscriptions

**Decision**

The main product object is the property, not the saved search.

**Rationale**

Searches are entry mechanisms. Users ultimately evaluate properties.

**Key implications / constraints**

Create `SearchSubscription` objects containing source, criteria, and notification policy.

Support manual property addition from day one using a URL or address.

---

## 18. Modular monolith for V1

**Decision**

Build a modular monolith rather than microservices.

**Rationale**

The domain is complex enough already. Distributed-service operational complexity is unnecessary at this stage.

**Key implications / constraints**

Use strong module boundaries within one Python application.

Keep browser, enrichment, and analysis workloads deployable separately where required, but share domain/application code.

---

## 19. MiniStack + DigitalOcean dogfood, AWS-compatible production path

**Decision**

Run the long-lived dogfood system on DigitalOcean using Docker Compose, MiniStack, and standalone Postgres while keeping AWS as the production compatibility target.

**Rationale**

This provides cheap, persistent dogfooding while preserving an AWS-native architecture.

**Key implications / constraints**

- MiniStack may emulate S3/SQS/EventBridge/Step Functions/etc.
- Use standalone Postgres instead of emulated RDS.
- Back up Postgres and object storage independently.
- Validate critical paths against real AWS before production.

---

## 20. Python backend, React/TypeScript frontend, TypeScript CDK

**Decision**

Use Python for backend/workers, TypeScript + React for the frontend, and TypeScript for AWS CDK.

**Rationale**

Python is the best fit for AI/data/experimentation and concise agent-written code. React/TypeScript remains the strongest fit for the UI and CDK ecosystem.

**Key implications / constraints**

Recommended backend stack:

- Python 3.13+
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- PostgreSQL
- Playwright
- boto3
- pytest

Keep boundaries language-neutral so Rust can be introduced later if justified.

---

## 21. Step Functions for durable workflows; SQS/EventBridge for simpler async work

**Decision**

Use AWS Step Functions for meaningful multi-step durable workflows. Use SQS/EventBridge where simple buffering or event delivery is sufficient.

**Rationale**

Property processing includes retries, fan-out, conditional branches, and long-running steps, but does not require Temporal in V1.

**Key implications / constraints**

- Keep state machines intentionally simple.
- Business logic belongs in Python.
- Typical workflow size: approximately 5–15 meaningful states.
- No Temporal for V1.

---

## 22. Dedicated MLS mailbox + IMAP ingress

**Decision**

Use a dedicated MLS mailbox and an IMAP polling adapter for V1.

**Rationale**

It is simple, reliable enough, and avoids coupling ingestion to Gmail-specific APIs.

**Key implications / constraints**

- Poll every ~15–30 seconds.
- Preserve original `.eml` as immutable evidence.
- No LLM is needed for email ingestion.
- Keep `EmailIngress` pluggable for SES/Gmail/webhook later.

---

## 23. Persistent authenticated TheMLS Playwright profile

**Decision**

Use one persistent authenticated TheMLS browser profile on the DigitalOcean dogfood host.

**Rationale**

TheMLS enrichment requires authenticated browser access, and maintaining one durable session is simpler than reauthenticating per job.

**Key implications / constraints**

Browser lifecycle states:

- `HEALTHY`
- `SESSION_EXPIRING`
- `AUTH_REQUIRED`
- `BLOCKED`

Pause dependent jobs and notify an administrator when reauthentication is required.

---

## 24. PostgreSQL relational core + JSONB for evolving intelligence

**Decision**

Use relational tables for stable domain entities and JSONB for evolving analyzer/ontology output.

**Rationale**

Core entities need strong relational integrity, while intelligence attributes will change rapidly during product development.

**Key implications / constraints**

Relational entities include:

- Property
- Listing
- Tenant
- SourceObservation
- Claim
- AnalysisRun
- PropertyEvaluation

Use JSONB for flexible analyzer payloads until attributes deserve promotion to first-class columns/tables.

---

## 25. Relational state + immutable history + transactional outbox

**Decision**

Do not use pure event sourcing. Keep relational current state authoritative, preserve immutable history/domain events, and use a transactional outbox for reliable event publication.

**Rationale**

This gives auditability and replay without forcing every read to rebuild state from an event log.

**Key implications / constraints**

For a state change such as a price update, one transaction should:

1. update current listing state
2. insert history
3. insert domain event
4. insert outbox event

Publish the outbox after commit.

---

## 26. Direct task execution, Lambda-first, Fargate escape hatch

**Decision**

Prefer direct task execution rather than an always-on worker fleet. Use Lambda first and on-demand Fargate RunTask for workloads unsuitable for Lambda.

**Rationale**

The system will spend much of its time idle. Zero-idle-cost compute fits the workload.

**Key implications / constraints**

- Step Functions may directly invoke Lambda.
- Browser/heavy/stateful tasks may use Fargate.
- SQS is a buffering/backpressure tool, not the default execution model.
- DigitalOcean should emulate this with on-demand processes/containers rather than permanent polling workers.

---

## 27. Serverless API in AWS

**Decision**

Deploy the application API as API Gateway + Lambda in AWS while running the same FastAPI app as a normal service/container on DigitalOcean.

**Rationale**

This preserves a single application implementation while matching the zero-idle-cost production philosophy.

**Key implications / constraints**

FastAPI is the sole application backend.

Use Fargate only where Lambda is a poor fit.

---

## 28. Cognito as initial identity provider

**Amended 2026-11-02 (user):** refined by A3 below (client-agnostic MCP access).

**Decision**

Use Cognito initially, while keeping application users/tenants independent from Cognito identities.

**Rationale**

Authentication provider choice should not own the domain model.

**Key implications / constraints**

Flow:

`Cognito JWT → API Gateway/FastAPI → internal ApplicationUser → tenant`

Keep an authentication abstraction so Cognito can later be replaced by Auth0, Clerk, Google, etc.

---

## 29. React + Vite + RTK Query + generated OpenAPI client + MCP adapter

**Amended 2026-11-02 (user):** refined by A3 below (client-agnostic MCP access).

**Decision**

Use React + TypeScript + Vite for the SPA, Redux Toolkit / RTK Query for API access, and generate the client from FastAPI’s OpenAPI document.

Also expose an MCP server as a parallel adapter over the same Python application/service layer.

**Rationale**

The API contract should be canonical and reused by both the web UI and agent tooling.

**Key implications / constraints**

Build chain:

`Pydantic → FastAPI → openapi.json → RTK Query codegen → React`

Use `@rtk-query/codegen-openapi`.

Do not hand-edit generated clients.

MCP should call application services directly, not proxy HTTP back into FastAPI.

---

## 30. Resource-oriented REST with explicit workflow actions

**Decision**

Use normal resource-oriented REST for CRUD/read operations and explicit action endpoints only for workflows.

**Rationale**

This keeps the API predictable without forcing workflow semantics into CRUD.

**Key implications / constraints**

Examples:

- `GET /properties`
- `GET /properties/{id}`
- `GET /properties/{id}/listings`
- `GET /properties/{id}/intelligence`
- `GET /properties/{id}/history`
- `GET /preferences`
- `PATCH /preferences/{id}`
- `POST /properties/{id}/feedback`
- `POST /properties/{id}/deep-analysis`
- `POST /properties/{id}/reanalyze`

---

## 31. Multi-provider AI abstraction

**Amended 2026-11-02 (user):** refined by A2 below (both OpenAI and Anthropic adapters initially).

**Decision**

Support multiple model providers from day one, while implementing only one or two initially.

**Rationale**

Models will change rapidly. Analyzer logic should request capabilities rather than hard-code vendors.

**Key implications / constraints**

Use abstractions such as:

- `ModelProvider`
- `ModelRouter`

Capabilities may include:

- `VISION`
- `STRUCTURED_OUTPUT`

Every `AnalysisRun` records provider, model, version, prompt/analyzer version, usage, cost, latency, and structured result.

---

## 32. Rich V1 property ontology

**Decision**

Start with a relatively rich ontology of roughly 75–150 standardized property attributes, plus extensible observations.

**Rationale**

Property understanding is expensive. It is better to extract a broad reusable profile once than repeatedly reanalyze because a new tenant cares about a previously ignored feature.

**Key implications / constraints**

The ontology should cover structure, rooms, layout, indoor/outdoor, yard, garage, condition, kitchen, architecture, location, risks, development potential, and related features.

---

## 33. Database-backed immutable/versioned ontology

**Decision**

Store ontology definitions in PostgreSQL as immutable versions with a draft/publish lifecycle.

**Rationale**

Ontology evolution should not require application deployment, but executable behavior should remain in code.

**Key implications / constraints**

Suggested tables:

- `ontology_versions`
- `ontology_attributes`

Version states:

- `DRAFT`
- `ACTIVE`
- `RETIRED`

Analysis runs must record the ontology version.

Do not create a programming language inside JSONB.

---

## 34. Global preference definitions reference the property ontology

**Decision**

Separate the global definition of how humans may care about property attributes from tenant-specific priorities.

**Rationale**

Property facts and buyer preferences are different layers.

**Key implications / constraints**

Model:

`Property Ontology → Preference Definitions → Tenant Preference Profile → Property Evaluation`

Tenant profiles contain importance, acceptable alternatives, goals, tradeoffs, and learned evidence.

---

## 35. Claim-level evidence and provenance are mandatory

**Decision**

Every inferred property intelligence claim must point to durable evidence.

**Rationale**

The product must be able to explain why it believes something, support corrections, and evaluate model quality.

**Key implications / constraints**

Evidence may refer to:

- specific photo
- image region
- floorplan region
- MLS text span
- assessor record
- permit record
- provider response

Use durable evidence IDs rather than only source URLs.

---

## 36. Human corrections are first-class claims

**Decision**

Human observations/corrections become claims with high authority for the appropriate attribute, while preserving the original AI output.

**Rationale**

Corrections are both product truth and valuable evaluation data.

**Key implications / constraints**

- Do not overwrite AI runs.
- Human corrections may outrank AI inference for subjective/visible attributes.
- Human corrections should not automatically override official legal/parcel facts without supporting evidence.

---

## 37. Selective human verification based on value of information

**Decision**

Do not ask users to verify every uncertain inference. Ask only when verification is likely to materially improve the decision.

**Rationale**

Verification burden should be proportional to expected value.

**Key implications / constraints**

Prioritize verification based on:

`uncertainty × preference importance × potential score/decision impact`

After a tour, ask a small number of high-value questions rather than a full checklist.

---

## 38. Valuation / market intelligence is a separate subsystem

**Decision**

Treat market valuation as its own reusable property intelligence subsystem rather than mixing it into lifestyle/property-quality analysis.

**Rationale**

A property can be an excellent lifestyle fit and still be overpriced.

**Key implications / constraints**

Market intelligence may include:

- comps
- active competition
- price history
- $/sqft context
- adjustments
- market conditions
- external AVMs
- internal valuation

Tenant evaluation consumes a separate `Value` dimension.

---

## 39. Deterministic, explainable comp selection first

**Decision**

Begin with an explainable weighted comp-selection algorithm and collect data to benchmark learned/AI-assisted approaches later.

**Rationale**

Comp selection affects valuation materially and needs to be understandable.

**Key implications / constraints**

Similarity features may include:

- micro-location
- sale recency
- sqft
- lot size
- bed/bath configuration
- architecture
- condition
- views
- pool
- garage
- layout quality

Always expose which comps were selected and why.

---

## 40. Valuation output is probabilistic

**Decision**

The canonical valuation output is a distribution/range/confidence, not a single authoritative point estimate.

**Rationale**

A point estimate hides uncertainty and creates false precision.

**Key implications / constraints**

Store and display:

- central estimate
- likely range
- confidence
- comparison to list price
- potentially probabilities of exceeding price thresholds

Two equal midpoints with different uncertainty must not receive identical value treatment.

---

## 41. Offer strategy is tenant-specific and sits above valuation

**Decision**

Separate “what the property is worth” from “what this buyer should offer.”

**Rationale**

Offer strategy depends on buyer utility, competition, financing, risk tolerance, and regret tolerance in addition to market value.

**Key implications / constraints**

Produce scenarios such as:

- value bid
- competitive bid
- win-focused bid
- walk-away threshold

Record outcomes where known for future learning.

---

## 42. Location intelligence is a reusable property subsystem

**Decision**

Model location intelligence independently of tenant preferences.

**Rationale**

Research the location once, then let each household decide how much each attribute matters.

**Key implications / constraints**

Location intelligence may include:

- walkability/bikeability
- parks
- schools
- retail/restaurants
- freeway/airport proximity
- hazards
- street classification
- lot position
- commercial/multifamily proximity
- streetscape

**Busy vs. quiet street is a first-class attribute.**

Suggested traffic attributes include:

- traffic level
- functional class
- estimated daily traffic
- through-traffic likelihood
- lane count
- speed limit
- signal/intersection proximity
- truck/commercial traffic
- cut-through risk
- cul-de-sac/dead-end status

---

## 43. Tenant-defined commute / travel-time intelligence

**Decision**

Allow tenants to define important destinations and evaluate each property against time-of-day-aware travel times.

**Rationale**

Travel time is often more meaningful than straight-line distance.

**Key implications / constraints**

Destinations may include:

- work
- school
- family
- gym
- airport
- other recurring destinations

Keep destination definitions tenant-private.

---

## 44. Content-addressed immutable media/evidence storage

**Decision**

Store retained media and evidence using content-addressed object storage and hash-based deduplication whenever source rights permit.

**Rationale**

Source URLs may expire and repeated listing versions may contain duplicate assets.

**Key implications / constraints**

Use SHA-256 or equivalent content hashes.

Separate immutable object bytes from relational provenance metadata.

Example evidence asset metadata:

- hash
- media type
- object key
- source URL
- provider
- captured time
- property/listing references

---

## 45. Evaluation and experimentation are first-class V1 capabilities

**Decision**

Build model/analyzer evaluation and shadow experimentation into V1 rather than adding it later.

**Rationale**

The system must be able to determine whether a change improves quality instead of relying on subjective impressions.

**Key implications / constraints**

Track:

- versioned runs
- human ground truth
- shadow results
- accuracy/error metrics
- cost
- latency

Apply the same framework to analyzers, comp selectors, valuation models, resolvers, triggers, and scoring strategies.

---

## 46. Three-layer testing strategy + golden property set

**Decision**

Use deterministic tests, replay tests, and live integration tests, plus a curated golden-property regression dataset.

**Rationale**

External dependencies and probabilistic models require more than conventional unit tests.

**Key implications / constraints**

1. Deterministic tests
2. Replay tests using retained MLS/email/provider/evidence fixtures
3. Live integration tests against providers and infrastructure

Maintain a carefully reviewed golden set of real properties for intelligence regression testing.

---

## 47. Single monorepo with independently deployable components

**Decision**

Use one monorepo organized by domain/component.

**Rationale**

Backend, frontend, OpenAPI generation, MCP tooling, CDK, fixtures, and ontology evolve together and benefit from atomic changes.

**Key implications / constraints**

Suggested top-level structure:

- `/backend`
- `/frontend`
- `/infrastructure`
- `/ontology`
- `/fixtures`
- `/specs`
- `/docs`

Monorepo does **not** imply one deployment. Components remain independently buildable/testable/deployable.

---

## 48. GitHub Actions + DigitalOcean dogfood + AWS staging + production

**Decision**

Use GitHub Actions for CI/CD, a reproducible DigitalOcean dogfood environment, real AWS staging, and eventual AWS production.

**Rationale**

Dogfood needs to behave like a real environment, while staging validates real AWS compatibility.

**Key implications / constraints**

CI should cover:

- Python lint/typecheck/tests
- frontend lint/typecheck/tests
- OpenAPI generation check
- RTK Query generation check
- replay/golden tests
- CDK synth
- selected integration tests

Use path-aware CI so unrelated changes do not rebuild everything.

---

## 49. Portable secrets/configuration architecture

**Decision**

Keep application configuration provider-neutral while using environment-appropriate secret stores.

**Rationale**

Application logic should request logical secrets/config values, not know how they are stored.

**Key implications / constraints**

AWS:

- Secrets Manager for secrets
- Parameter Store/environment for normal config
- GitHub OIDC for CI deployments

DigitalOcean:

- deployment-injected secrets/config

Treat authenticated browser session state as credential material.

Never place secrets in prompts, logs, fixtures, evidence, or analysis records.

---

## 50. OpenTelemetry + structured JSON logging

**Decision**

Use OpenTelemetry and structured JSON logging as the application observability standard.

**Rationale**

The same instrumentation should work on DigitalOcean and AWS without tying the application to a paid vendor.

**Key implications / constraints**

AWS destination: CloudWatch.

DigitalOcean destination: lightweight self-hosted observability stack.

Trace/correlation should connect:

`MLS email → ingestion → property → workflow → analysis run → model call → claim → notification`

---

## 51. Email + in-app notifications for V1

**Decision**

Implement email and in-app notifications first.

**Rationale**

They cover immediate alerting and durable product history without overbuilding notification infrastructure.

**Key implications / constraints**

Use a pluggable `NotificationStrategy` so SMS, Slack, push, and webhooks can be added later.

Notifications link back to the canonical property page.

---

## 52. Automated backups and restore testing

**Decision**

Back up PostgreSQL and object/evidence storage automatically, keep off-server copies, use retention policies, and test restoration periodically.

**Rationale**

DigitalOcean dogfood is a real operational environment and must not be a single point of failure.

**Key implications / constraints**

Suggested baseline:

- daily backups
- off-server storage
- 30-day daily retention
- longer-lived monthly snapshots
- periodic restore drills

Infrastructure/config should remain reproducible from Git.

---

## 53. Partial results are first-class

**Decision**

A property should become usable as soon as enough data exists. Missing enrichment must not block the entire pipeline.

**Rationale**

External providers fail independently and some data may never exist.

**Key implications / constraints**

- Missing data is `UNKNOWN`, not false.
- Track intelligence completeness.
- Retry individual capabilities independently.
- Recompute only dependent analysis when new data arrives.
- Support `PARTIAL` analysis status.

---

## 54. Idempotency is a system-wide invariant

**Decision**

Design ingestion, enrichment, analysis, workflow execution, and notifications to be safely retryable.

**Rationale**

Emails duplicate, workflows retry, providers time out after succeeding, and distributed systems can deliver work more than once.

**Key implications / constraints**

Use:

- stable idempotency keys
- database uniqueness constraints
- content hashes
- computation identity based on inputs + versions

Changing source evidence, model, analyzer, prompt, or ontology version should intentionally produce a new run.

---

## 55. Explicit failure taxonomy + persisted failed-work state + operations UI

**Decision**

Classify and persist failures rather than relying only on infrastructure logs or DLQs.

**Rationale**

Operational failures must be visible and actionable in the product.

**Key implications / constraints**

Failure classes include:

- `TRANSIENT`
- `AUTH_REQUIRED`
- `DATA_UNAVAILABLE`
- `INVALID_INPUT`
- `PERMANENT`
- `BUG / UNKNOWN`

Use bounded retries/backoff.

Persist failed work and expose operational status in an admin/operations UI.

---

## 56. Per-property / per-run cost attribution and budgets

**Decision**

Track cost at the property, workflow, analyzer, model, and provider level from V1.

**Rationale**

Deep property intelligence can involve multiple paid APIs and AI calls. Cost must be measurable alongside quality and latency.

**Key implications / constraints**

Track costs such as:

- browser/enrichment compute
- vision analysis
- text analysis
- maps/location APIs
- property-data APIs
- deep research
- tenant matching

Support configurable budgets by analysis tier rather than embedding cost limits inside individual analyzers.

Evaluation should compare:

`quality × cost × latency`

---

# Amendments

Confirmed by the user on 2026-11-02. Each amendment names the decision it changes; the original decision text above is kept unchanged for history. Where an amendment and its original decision conflict, the amendment wins.

---

## A1. Individual accounts with shareable searches (amends D5)

**Decision**

Each person signs up for their own account. A user can share a search subscription with other users, for example a spouse, who then collaborate on it. This replaces D5's model of a tenant as a household with one shared active preference profile.

**Rationale**

The user chose individual accounts plus sharing ("let's have each user create an account and then you can share a search with your spouse etc."). People in one household keep their own preferences while working on a common search.

**Key implications / constraints**

- Each user's private data (their preference profile, feedback and notes) is private to them.
- A shared search's criteria, evaluations, shortlist and visit questions are visible to and editable by its members.
- Sharing is by invitation to an existing or new account.
- A search has one owner; everyone else on it is a member. No other roles.
- Removing a member revokes that member's access to the search.
- D5's surviving parts still hold: tenant isolation from day one, no billing/org/RBAC complexity, and global property facts with private interpretation.

---

## A2. OpenAI and Anthropic adapters initially (refines D31)

**Decision**

Implement both OpenAI and Anthropic model-provider adapters initially, behind `ModelProvider` / `ModelRouter`.

**Rationale**

D31 allows one or two initial implementations; the user chose two.

**Key implications / constraints**

- Both adapters satisfy the same `ModelProvider` contract.
- `ModelRouter` selects a provider by capability and configuration; analyzers never name a vendor.

---

## A3. Client-agnostic MCP access over OAuth (refines D28, D29)

**Decision**

MCP access is client-agnostic: any MCP client that supports the MCP authorization specification (OAuth) can connect. There are no client-specific integrations.

**Rationale**

The user: "shouldn't matter — we'll use OAuth." Standard OAuth removes the need to choose or support particular MCP clients.

**Key implications / constraints**

- The MCP adapter follows the MCP authorization specification, with Cognito (D28) as the authorization server.
- No code, configuration or tests target a particular MCP client.

---

# Cross-cutting architectural principles

The following principles recur across multiple decisions and should guide implementation when a task is ambiguous:

1. **Property facts are global; tenant interpretation is private.**
2. **Unknown is not false.**
3. **Preserve evidence and provenance.**
4. **Prefer deterministic logic where deterministic logic is sufficient.**
5. **AI explains and infers; it should not silently own business rules.**
6. **Version every meaningful computation.**
7. **Do expensive property understanding once and reuse it.**
8. **Design for retries and partial failure.**
9. **Prefer simple operational architecture until scale requires more.**
10. **Every abstraction should exist because a real implementation needs it, not because the system might need it someday.**

---

# Relationship to the Master Plan

This decision register is intended to be referenced by the master plan as the canonical architecture-decision list.

The master plan should explain the architecture as a coherent technical narrative and execution specification. This document preserves the planning decisions individually so implementation agents can verify that task decomposition does not accidentally omit, contradict, or reinterpret a confirmed decision.
