---
artifact: plan
id: <NNN-short-name>
spec: specs/<NNN-short-name>/spec.md
status: draft            # draft | approved
approved_by: ""
approved_on: ""
---

# Plan <NNN>: <feature name>

How the spec will be built. Every decision cites the requirement it serves and the
constitution articles it satisfies.

## 1. Technical context
- Stack: <from PRODUCT-LINE.md>
- Storage: <...>
- External services and their twins: <...>

## 2. Constitution check
| Article | Satisfied? | How / approved exception |
|---|---|---|
| 1 Stack | | |
| 2 Tests first | | |
| 3 External services behind interfaces | | |
| 4 Data and privacy | | |
| 5 Security | | |
| 6 Simplicity | | |
| 7 Observability | | |

## 3. Architecture
<Modules, boundaries, data flow. A diagram if it helps.>

## 4. Data model
<Tables, columns, constraints; migrations are expand/contract.>

## 5. Interfaces
<HTTP endpoints, request and response shapes, error codes.>

## 6. Risk and tier
<Which parts are load-bearing (Tier 3, human only), which are Tier 2 (UI, public API),
which are Tier 1.>

## 7. Test strategy
<Unit, integration, end-to-end click-through (Playwright), accessibility (axe), twins.>
