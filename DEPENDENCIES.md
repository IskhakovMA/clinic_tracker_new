# Reproducible Python 3.13 dependencies (WP-F)

Supported artifact targets: CPython 3.13, Linux x86_64 (Ubuntu CI, manylinux
2.17/2.28/2.34) and macOS arm64. Other interpreters/platforms are not claimed
verified. No source-distribution build is permitted by these requirement files.

- backend/requirements.in: direct runtime decisions, unchanged from e05d1bb.
- backend/requirements-dev.in: additional development decisions.
- backend/requirements.txt: complete runtime closure, 24 exact packages.
- backend/requirements-dev.txt: runtime include plus dev closure, 57 total.
- .github/requirements-security.in/.txt: 35 exact CI-security packages.

Each install file enforces --require-hashes and --only-binary=:all:. SHA-256
values cover the actual selected wheels for both targets; shared universal
wheels share a hash. This is not an assertion about every wheel/sdist published
for a version. Unsupported artifacts fail closed instead of compiling code.

Install into a NEW venv (never use --ignore-installed, which can leave duplicate
bootstrap-pip dist-info directories):

```
python3.13 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python tools/ci/check_dependency_locks.py --profile dev
```

For a runtime-only environment install backend/requirements.txt and check the
runtime profile. For security jobs use .github/requirements-security.txt and
check the security profile. CI uses the same hash-enforcing install files; it
does not replace the security gates or ignore advisories.

Only two direct toolchain versions changed: pip 25.1.1 → 26.2.2 and pytest
8.3.4 → 9.0.3. pip-audit identified six advisories for the former pip and one
for pytest (PYSEC-2026-196, -1795, -1796, -2875, -2876, -3721; pytest
PYSEC-2026-1845). Updates meet the reported fixed versions; the direct runtime
versions, pip-audit 2.7.3, Bandit 1.8.6 and policy PyYAML 6.0.2 remain unchanged.
The original runtime PyYAML selection is 6.0.3; security jobs use a separate
pinned environment, so the existing security-tool version does not conflict.

## Review/update procedure

1. Change only explicitly reviewed direct versions in .in; keep unrelated
   resolved versions as constraints. Resolve in a clean Python 3.13 environment.
2. Review the complete resolved package/version diff, including environment
   markers and extras (uvicorn standard). Do not blindly freeze an unrelated
   host environment or upgrade every library.
3. Download binary wheels of EVERY resolved pin separately for Linux x86_64
   and macOS arm64 with pip download --no-deps --only-binary=:all: and explicit
   --python-version 3.13 / --implementation cp / --abi cp313,abi3,none targets.
   Record SHA-256 of each selected artifact. Do not invent hashes or simply
   accept a changed artifact without reviewing it.
4. Update complete .txt entries with the approved exact versions and hashes;
   keep --require-hashes and --only-binary. Dev includes runtime; security is
   independent. No credentials, local cache URLs or trusted-host bypasses.
5. Install again from the final requirement files into new environments;
   pip check; run check_dependency_locks for the applicable profile. It checks
   root decisions, hash syntax, installed versions and full Requires-Dist
   closure for Linux and macOS markers.
6. Re-download/install both target artifact sets with hash checking. Native
   execution on macOS is distinct from cross-target Linux installation. The
   latter proves wheel resolution/hashes, not a native Linux test run.
7. Run pip-audit --require-hashes -r on runtime, dev and security requirements,
   then all project tests/security gates. Keep before/after evidence and review.

The lock does not pin the OS, Python executable, CA store or service manager.
Use an approved Python 3.13 installation. The requirements pin the pip tool
used after installation; bootstrap Python/pip are the operator's trust root.
No Linux VM/container daemon was installed or started in this task. Linux
native execution must not be claimed from cross-target checks.

Sources: https://pip.pypa.io/en/stable/topics/secure-installs/ and
https://pip.pypa.io/en/stable/cli/pip_download/ . Hash verification protects
selected artifacts; it does not independently prove publisher trust or absence
of vulnerabilities. Security audits remain mandatory.
