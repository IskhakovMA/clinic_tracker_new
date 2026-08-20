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
| Docker client version | `29.5.3` |
| Docker server | Docker Desktop `4.77.0`, engine `29.5.3` |
| Docker context | `desktop-linux` |
| Strix CLI | `strix 1.5.3` in `/tmp/clinic-strix-v2-venv` |
| Strix sandbox image | `ghcr.io/usestrix/strix-sandbox:1.3.0` |
| Local OpenAI-compatible endpoint | `http://127.0.0.1:1234/v1/models` reachable |
| Local model | `qwen3-8b` |
| LiteLLM model identifier | `lm_studio/qwen3-8b` |
| External Strix cloud mode | Not used |
| External LLM provider | Not used |
| Strix autofix | Not used |

## Scan Attempts

### White-box

- Target: `/Users/maksutishakov/Documents/clinic_tracker_new`
- Mode: quick, non-interactive, full scope, max turns 20, max budget 0.25
- Run directory: `/private/tmp/clinic-strix-v2-runs-20260820133159/strix_runs/clinic-tracker-new_354f`
- Status: failed
- Failure: LM Studio/OpenAI-compatible API returned context overflow: `request (41306 tokens) exceeds the available context size (40960 tokens)`.
- SARIF result count: `0`

### Dynamic localhost

- Synthetic app target: `http://127.0.0.1:8431`
- Sandbox target mapping: `http://host.docker.internal:8431`
- Mode: quick, non-interactive, max turns 20, max budget 0.25
- Synthetic database: `/tmp/clinic-strix-dynamic-data.*`
- Synthetic roles created: developer, doctor A, doctor B, department head A, department head B, read-only
- Run directory: `/private/tmp/clinic-strix-v2-dynamic-runs-20260820134031/strix_runs/host-docker-internal-8431_6385`
- Status: interrupted after no useful model response
- Evidence: Strix log recorded `model stream produced no event for 300s; abandoning the turn`, then retry; the run was stopped while waiting for the first model response.
- SARIF result count: `0`

## Data Handling

Raw Strix artifacts remained outside tracked production paths under `/private/tmp/clinic-strix-v2-*`. No Strix files, LLM config, synthetic credentials, Docker config, model files, or raw run artifacts were added to the application repository. Strix managed cloud and external LLM providers were not used.

The Strix logs contain built-in telemetry events named `skill_loaded` for PostHog/Scarf. The sanitized evidence did not show project source, patient data, synthetic credentials, or vulnerability findings being sent in those log lines, but this confirms the local CLI is not completely silent telemetry-wise.

## Findings

No Strix candidate findings were generated. This is not a clean vulnerability result because the scans did not complete:

- White-box failed before producing an agent result.
- Dynamic localhost scan did not get a useful first model response and was interrupted.

No `SEC-V2-004` finding was created.

## Retest

To retest later:

1. Confirm Docker Desktop with `docker info`.
2. Start a local or explicitly approved OpenAI-compatible LLM provider.
3. Use a model/context configuration that can complete Strix's root-agent prompt.
4. Run Strix in local/headless mode against the repository and a localhost synthetic application instance.
5. Store raw artifacts outside tracked production paths.
6. Manually validate every Strix candidate before assigning an internal `SEC-V2-*` finding.
