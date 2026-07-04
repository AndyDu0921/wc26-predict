# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please **do not** open a public issue.

Instead, report it privately:

1. **Email**: Open an issue with the title `[SECURITY]` and we will exchange contact information
2. Include a clear description of the vulnerability, steps to reproduce, and potential impact

## Scope

Security concerns relevant to this project include:

- **API key exposure** — accidental commits of `.env` files or hardcoded credentials
- **Dependency vulnerabilities** — outdated packages with known CVEs
- **Data leakage** — prediction pipeline accessing future information during backtesting
- **Input injection** — malicious inputs through API endpoints (match names, team names)

## Best Practices for Contributors

- Never commit `.env`, `.env.local`, or any file containing API keys
- Run `git status` before committing to check for accidental sensitive file inclusion
- Rotate API keys immediately if they are ever exposed (even in private repos)
- Keep dependencies updated — check `requirements.txt` for known-vulnerable versions
- Validate and sanitize all user inputs in API endpoints

## Supported Versions

Only the latest version on the `master` branch receives security updates.

| Version | Supported |
|:---|:---|
| V4.7.0-alpha (master) | ✅ |
| All older versions | ❌ |

## Security-Related Configuration

- `ADMIN_TOKEN` **must** be changed from the default `change-me` before any deployment
- API routes under `/api/admin/*` require `ADMIN_TOKEN` authentication
- Market API keys should use minimal-scope tokens where the provider supports it

---

This project takes the security of research data and API credentials seriously. Thank you for helping keep it secure.
