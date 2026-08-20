# Strix V2 Sanitized Evidence

Audit date: 2026-08-20
Project branch: `audit/security-v2`
Base commit: `527cba5c194e55ff7b0f4d32cddde2708896c699`

## Tool Source

Strix repository was cloned outside the production repository:

- Local path: `/tmp/clinic-security-audit-v2-tools/strix`
- Commit: `6f88b7d7d5b6f57833b73a5384079e229e21aa2f`

No Strix files were copied into the application source tree.

## Readiness Checks

| Check | Result |
| --- | --- |
| Docker CLI | Present: `/usr/local/bin/docker` |
| Docker version | `29.5.3` |
| Docker daemon | Not available in this shell session |
| Local OpenAI-compatible endpoint | `http://127.0.0.1:1234/v1/models` unreachable |
| External Strix cloud mode | Not used |
| External LLM provider | Not used |
| Strix autofix | Not used |

## Decision

Strix white-box and dynamic scans were not run because local prerequisites were missing. The audit instructions required local/self-hosted mode preference and no automatic external LLM/cloud submission. Therefore no project source, synthetic database, prompts, secrets, or scan artifacts were sent to Strix cloud or any external model provider.

## Findings

No Strix findings exist for this audit pass because no scan output was generated.

## Retest

To retest later:

1. Start Docker Desktop and confirm `docker info` succeeds.
2. Start a local or explicitly approved OpenAI-compatible LLM provider.
3. Run Strix in local/headless mode against the repository and a localhost synthetic application instance.
4. Store raw artifacts outside tracked production paths.
5. Manually validate every Strix candidate before assigning an internal `SEC-V2-*` finding.
