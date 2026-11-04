---
artifact: prfaq
product: <product name>
status: draft            # draft | approved | killed
approved_by: ""          # GitHub login of the approving human
approved_on: ""          # YYYY-MM-DD
---

# <Product name> — press release and FAQ

Working backwards (spec §10.5, LIFE-10): write the announcement for the finished product first.
If it is not worth announcing, it is not worth building. The kill gate at the end decides.

## Press release

**Headline.** <One sentence a customer would repeat.>

**Subheading.** <Who it is for and the one benefit.>

**Problem.** <The customer's problem today, in their words. No solution language.>

**Solution.** <What the product does about it, at the level a customer sees.>

**Quote from the team.** <Why we built it. Fictional and labelled as such.>

**How it works.** <Three to five steps from the customer's point of view.>

**Customer quote.** <What a satisfied customer would say. Fictional and labelled as such.>

**Call to action.** <How to start.>

## External FAQ (customers)

| Question | Answer |
|---|---|
| | |

## Internal FAQ (us)

| Question | Answer |
|---|---|
| Who exactly is the first customer? | |
| What is the smallest version worth shipping (v1)? | |
| What is explicitly out of scope for v1? | |
| Which external services does it depend on, and what happens when they fail? | |
| What are the legal, privacy and safety risks? | |
| How will we know it works (success metrics, with numbers)? | |
| What would make us stop (kill criteria)? | |
| Does it fit the factory's product line (`docs/factory/PRODUCT-LINE.md`)? | |

## Kill gate

```
KILL_GATE:
  fits_product_line: yes | no
  first_customer_named: yes | no
  v1_scope_bounded: yes | no
  external_dependencies_have_fallbacks: yes | no
  risks_have_owners: yes | no
  success_metric_measurable: yes | no
DECISION: proceed | revise | kill
```

A human sets `DECISION`. Any `no` above means `revise` or `kill`. The decision, with the date,
goes into `docs/factory/DECISIONS.md`; killed briefs are kept (the kill rate is a metric, §10.5).
