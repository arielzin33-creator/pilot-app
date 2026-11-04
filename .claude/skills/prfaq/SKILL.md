---
name: prfaq
description: Draft or review a PR/FAQ (working-backwards brief) for a new product or a large feature, and evaluate its kill gate. Use when someone proposes a new product, app or major capability, or asks whether an idea is worth building.
---

# PR/FAQ brief (Phase E, gate 1 of 4)

1. Copy `.factory/templates/intake/PRFAQ.md` to `docs/product/PRFAQ.md` (new product) or
   `specs/<NNN-name>/PRFAQ.md` (large feature).
2. Interview the owner before writing: ask for the first customer, the problem in the
   customer's words, the smallest version worth shipping, what is out of scope, external
   dependencies, risks, success metrics with numbers, and kill criteria. Ask one topic at a
   time; do not invent answers. Unknowns stay as explicit questions in the internal FAQ.
3. Write the press release last-to-first: customer quote and FAQ answers constrain the headline.
   Fictional quotes are labelled fictional.
4. Fill the kill gate honestly. Any `no` means `DECISION: revise` or `kill`; only the owner
   sets `proceed` and `status: approved`.
5. Run `python3 -I .factory/scripts/intake.py prfaq-check <path>` and fix every error.

Never approve your own brief. Never set `approved_by`.
