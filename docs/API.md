# API

All responses are JSON unless noted. Errors have the shape `{"error": "<message>"}` and the
header `content-type: application/json`.

## Links

### `POST /links`

Create a link. Body: `{"slug": "<slug>", "target": "<url>", "ttlSeconds": <optional number>}`.

- `slug`: 3 to 32 characters; lowercase letters, digits and hyphens.
- `target`: an absolute `http` or `https` URL, at most 2048 characters.
- Responses: `201` with the link; `400` for invalid input or a body that is not valid JSON;
  `409` if the slug already exists.

### `GET /links`

List links, newest first. Query: `page` (default 1), `limit` (default 20, maximum 100).
Response: `{"items": [...], "page": 1, "limit": 20, "total": 3}`. You will recieve at most
`limit` items per page.

### `GET /links/:slug`

Return one link, or `404`.

## Redirects

### `GET /r/:slug`

Redirect (`302`) to the target and count one click. Expired links return `404` and are not
counted. Clicks are stored seperate from the link's other fields.

## Stats

### `GET /stats`

Response: `{"links": <count>, "totalClicks": <n>, "averageClicks": <n>}`. With no links,
`averageClicks` is `0`.
