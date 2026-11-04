# Design-system rules (Phase E design stage)

Applies to any change under `src/ui/**`, `web/**`, `*.tsx`, `*.css`.

- Colours, spacing, radii, type and shadows come only from design tokens
  (`design/tokens.json`, W3C DTCG format), consumed as CSS custom properties from the generated
  `tokens.css`. No literal hex colours or pixel sizes in components.
- Regenerate tokens with `python3 -I .factory/scripts/intake.py tokens design/tokens.json --css <out> --ts <out>`;
  never edit generated files.
- Every interactive element is reachable by keyboard, has an accessible name, and passes the
  axe check in the end-to-end suite (WCAG 2.2 AA).
- New components get a story (or a fixture page) so the click-through and visual checks can
  reach them.
- When a Figma file is connected, Code Connect mappings live next to components; without one,
  tokens are the source of truth.
