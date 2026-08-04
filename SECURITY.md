# Security Policy

## Supported Version

Security fixes are prepared against the current `main` branch. Older local
branches are not supported as independent release lines unless the maintainer
explicitly marks one as supported.

## Reporting a Vulnerability

Use GitHub's private vulnerability reporting or Security Advisory flow when it
is available for this repository. If it is not available in the current GitHub
plan, contact the repository owner through a private channel already agreed with
the maintainer.

Do not publish vulnerabilities, secrets, patient data, screenshots with PHI,
databases, backups, logs, session tokens, CSRF tokens, or `.env` values in public
issues, pull requests, comments, or artifacts.

Please include:

- a concise description;
- affected component or endpoint;
- safe reproduction steps using synthetic data only;
- expected and actual behavior;
- suggested severity, if known.

Do not include real medical data, real phone numbers, production URLs, raw
secrets, or exploit payloads that could harm third parties.

## Response Expectations

The maintainer should acknowledge a private report within 5 business days when
available. Remediation timing depends on severity, reproducibility, and
deployment risk.

## Coordinated Disclosure

Please allow time for validation and remediation before public disclosure.
Active testing requires explicit authorization and must not target production or
real patient data unless a separate written scope says otherwise.
