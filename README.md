# pilot-links

Pilot application for the software factory: a small URL-shortener HTTP service on the factory
reference stack (TypeScript, Node.js 22, npm, Vitest, ESLint).

## Run

```
npm ci
npm run build
npm start        # listens on http://localhost:3000 (override with PORT)
```

## Verify

```
./.factory/scripts/gates.sh full
```

API reference: [docs/API.md](docs/API.md).

This repository is worked by the software factory. See `docs/factory/` and `AGENTS.md`.
