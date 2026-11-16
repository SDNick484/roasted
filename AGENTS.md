# AGENTS.md

## Verification Gate
Every change must pass the verification gate before completion:
```bash
python -m pytest -q tests && node --check static/app.js && sh -n ops/roasted-backup installer/build.sh installer/setup-head.sh
```

## Vendored Files
Do not edit vendored files under:
- `static/leaflet/*`
- `static/fonts/*`

## Contribution Guidelines
- Update `README.md` when user-facing workflows or endpoints change.
- Add or update tests under `tests/` for any new behavior or bug fix.
- Public repository hygiene: never add secrets, tokens, or personal addresses/domains.
- Private fields (`price`, `currency`, `notes`, `lat`, `lng`, `added_by`, `geo_tried` on bags; `lat`, `lng`, `geo_tried` on roasters) must never appear in public `/api/*` responses.

## PR Conventions
- Open PRs against `main`.
- Include `Closes #<N>` on the first line of the PR description.
