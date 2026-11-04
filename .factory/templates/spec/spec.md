---
artifact: spec
id: <NNN-short-name>
product: <product name>
status: draft            # draft | approved
approved_by: ""
approved_on: ""
constitution: docs/product/CONSTITUTION.md
---

# Spec <NNN>: <feature name>

What and why only. No technology choices: those belong in `plan.md` (Spec Kit, LIFE-15).
Mark every open question inline as `[NEEDS CLARIFICATION: <question>]`. The spec cannot be
approved while any marker remains (`factory.py spec-check`).

## 1. Problem and users
<Who has the problem, and what they do today.>

## 2. User stories
Each story has an id and acceptance scenarios in Given/When/Then form.

### US-1: <title>  (priority: P1)
As a <user>, I want <capability> so that <outcome>.

Acceptance scenarios:
1. **Given** <state>, **When** <action>, **Then** <observable result>.

## 3. Functional requirements
- **FR-001**: The system MUST <behaviour>.

## 4. Non-functional requirements
- **NFR-001**: <performance, accessibility, privacy, reliability, with a number>.

## 5. Key entities
<Names and relationships, not tables.>

## 6. Out of scope
- <explicitly excluded>

## 7. Success criteria
- **SC-001**: <measurable outcome>.

## 8. Review checklist
- [ ] No implementation details (languages, frameworks, APIs)
- [ ] Every requirement is testable and unambiguous
- [ ] Every user story has acceptance scenarios
- [ ] Success criteria are measurable
- [ ] No `[NEEDS CLARIFICATION]` markers remain
