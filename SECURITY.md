# Security Policy

## Reporting

Do not publish credentials, exploitable payloads, private evidence, or match-provider tokens in a public issue. Use the repository host's private security advisory channel, or contact the maintainer privately, and include impact, reproduction steps, and the affected revision.

## Supported Code

Security fixes target the latest revision on `master` and the active V4.12 hardening branch. Historical alpha/beta releases are retained for audit only.

## Runtime Contract

- `ADMIN_TOKEN` must be a generated secret of at least 32 characters. Known defaults and weak values are rejected at request time and application startup.
- Prediction and analysis mutation routes require bearer authentication and rate limits.
- Wildcard CORS is rejected while credentialed requests are enabled.
- `.env`, `.env.local`, `backend/.env`, database backups, and runtime model files must not be committed.
- Market API keys should use the narrowest provider scope available.
- Public reports may contain market odds as research evidence, but betting instructions, guaranteed outcomes, and stake language are prohibited.

## Data And Model Safety

- Pre-match strict features may only use evidence whose `available_at` is no later than the prediction freeze time and kickoff.
- Post-match events, player statistics, and results must never be joined into the same match's pre-match feature snapshot.
- API/worker prediction currently requires one aligned SQLite database. Postgres or mismatched sync/async paths fail closed to prevent split persistence.
- Required production model files are loaded only through `backend/artifacts/active_bundle.json`. The exact bytes are path-confined, size-checked, and SHA-256 verified before trusted local pickle deserialization.
- Treat `active_bundle.json` as trusted deployment configuration: runtime identities should have read-only access to the manifest and registered artifacts.
- Missing or tampered required artifacts stop prediction. Runtime inference must never retrain a replacement model implicitly.
- Training writes an immutable `candidate_unvalidated` bundle. Activation requires same-cohort temporal evidence and explicit human promotion.
- Tournament simulation must fail when a component prediction fails; placeholder probabilities are prohibited.

## SQL Construction Contract

- All external values use SQLite parameters (`?`), never string interpolation.
- Dynamic `IN` lists may generate only `?` placeholders.
- Dynamic table/column identifiers must be fixed internal allowlists, schema-discovered identifiers, or safely quoted identifiers; request values may never select an identifier.
- Bandit B608 findings require source review under this contract. They are not blanket-suppressed so a new interpolation site remains visible.

## Required Checks

```powershell
backend/.venv/Scripts/python.exe backend/scripts/verify_env.py
backend/.venv/Scripts/python.exe backend/scripts/audit_entrypoints.py --json
backend/.venv/Scripts/python.exe backend/scripts/audit_public_outputs.py
backend/.venv/Scripts/python.exe backend/scripts/audit_db_integrity.py
backend/.venv/Scripts/python.exe -m bandit -r backend/app backend/scripts
$env:PYTHONUTF8='1'; backend/.venv/Scripts/python.exe -m pip_audit -r backend/requirements.txt
```

Rotate any credential immediately after suspected exposure, even when the repository is private.
