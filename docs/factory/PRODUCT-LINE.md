# Product line and reference stack

**Status: DRAFT for the owner's decision.** Phase A depends on this document (spec §10.1, §1.2).
The factory onboards only repositories on the reference stack while the factory is at version
0.x; a second language runtime waits (spec §9.4). History supports this: SDC's factory failed
partly because "every job was totally different" across heterogeneous environments (HIST-12),
and the Japanese factories standardized one product family first (HIST-13).

## 1. The family of applications

| Question | Draft answer | Owner decision |
|---|---|---|
| What kinds of applications will the factory build? | Web applications and HTTP services for small businesses: a TypeScript back end, a web front end, a relational database | ☐ confirm / change |
| What is out of scope for 0.x? | Native mobile apps, embedded, ML training pipelines, anything regulated (medical, payments processing beyond a hosted provider) | ☐ confirm / change |
| Who reviews and merges? | The owner, alone. Tier 3 never runs unattended | ☑ decided 2026-11-04 |

## 2. Reference stack

| Layer | Choice (draft) | Why |
|---|---|---|
| Language | TypeScript, strict mode | One language front and back; strong types make the `types` gate meaningful |
| Runtime | Node.js 22 LTS | Matches the factory's tooling; long support window |
| Package manager | npm with a committed `package-lock.json` | Deterministic installs (`npm ci`); one lockfile format |
| Tests | Vitest | Fast, TypeScript-native |
| Lint | ESLint flat config with typescript-eslint | Standard; machine-readable output |
| Build | `tsc` | No bundler in services; front end adds Vite in Phase B |
| Front end (Phase B) | React with Vite; Storybook for visual review | Visual regression and click-through arrive in Phase B |
| End-to-end (Phase B) | Playwright | Traces, screenshots and video as evidence (TOOL-134) |
| Database (Phase D) | PostgreSQL; migrations linted before deploy | Migration lint arrives with release automation |

## 3. Required package scripts in every repository

`gates.sh` runs these; they are load-bearing once set.

| Script | Gate |
|---|---|
| `typecheck` | types |
| `lint` | lint |
| `test` | test |
| `build` | build |

## 4. Decision

```
PRODUCT_LINE_STATUS: draft   # change to "approved" with the date when the owner confirms
APPROVED_ON: <YYYY-MM-DD>
```
