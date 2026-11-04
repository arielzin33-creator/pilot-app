---
artifact: constitution
product: <product name>
status: draft            # draft | approved
approved_by: ""
approved_on: ""
---

# <Product name> constitution

The non-negotiable principles every spec, plan and change for this product must satisfy
(Spec Kit's constitution, LIFE-15). Specs cite articles by number; the spec check fails if a
plan contradicts an article without an approved exception in `DECISIONS.md`.

## Article 1 — Stack
The product uses the factory reference stack (`docs/factory/PRODUCT-LINE.md`). No second
language runtime.

## Article 2 — Tests first
Every behaviour in a spec has an acceptance test written before or with the code. A bug fix
starts with a failing test.

## Article 3 — External services behind interfaces
Every external service (AI models, payments, email, storage) is reached through an interface
with a dependency twin used in tests and local development. The product runs end to end with
no network.

## Article 4 — Data and privacy
<What data the product holds, retention, deletion, consent. Personal data is minimised.>

## Article 5 — Security
Authentication, authorisation and payment code are load-bearing: changed only by humans,
reviewed under CODEOWNERS.

## Article 6 — Simplicity
The smallest design that meets the spec. A new dependency, service or abstraction needs a
reason written in the plan.

## Article 7 — Observability
Every request and job emits a structured log line with a request id; errors carry the release.

## Article 8 — <product-specific principle>
