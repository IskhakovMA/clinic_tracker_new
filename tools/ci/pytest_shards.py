"""Split the backend test suite into CI shards and prove that all of it ran.

    collect  --out DIR                 collect every test once; write DIR/collected.txt
    files    --shard K --shards N      print the test files of shard K (1-based)
    verify   --collected FILE --results DIR [--expected-count N]

Sharding is by test file, which keeps a file's module-level state and fixtures
together. Files are assigned deterministically by test count (largest first,
each to the currently smallest shard; ties by path), so every shard job gets
the same plan from the same code. No test is skipped, deselected or ignored:
a shard runs `pytest <its files>`.

`verify` reads the outcome files written by tools/ci/pytest_outcomes.py in each
shard and fails unless
  * the union of the shards' collections equals the full collection,
  * no test was collected or executed by two shards,
  * every collected test has exactly one outcome and it is `passed`
    (no failures, errors, skips or missing results),
  * optionally, the total equals --expected-count.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

TEST_ROOT = "backend/tests"


def collect_node_ids(root: str = TEST_ROOT) -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", root, "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, check=False,
    )
    ids = [line.strip() for line in proc.stdout.splitlines() if "::" in line and not line.startswith(" ")]
    if proc.returncode != 0 or not ids:
        sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-4000:])
        raise SystemExit("test collection failed")
    if len(ids) != len(set(ids)):
        raise SystemExit("test collection produced duplicate node ids")
    return ids


def plan(node_ids: list[str], shards: int) -> list[list[str]]:
    if shards < 1:
        raise ValueError("shards must be >= 1")
    per_file = Counter(node_id.split("::", 1)[0] for node_id in node_ids)
    buckets: list[list[str]] = [[] for _ in range(shards)]
    loads = [0] * shards
    for path, count in sorted(per_file.items(), key=lambda item: (-item[1], item[0])):
        target = min(range(shards), key=lambda i: (loads[i], i))
        buckets[target].append(path)
        loads[target] += count
    return [sorted(bucket) for bucket in buckets]


def verify(collected: list[str], shard_results: dict[str, dict], expected_count: int | None = None) -> list[str]:
    problems: list[str] = []
    full = set(collected)
    if len(full) != len(collected):
        problems.append("full collection contains duplicate node ids")
    seen_collect: Counter = Counter()
    outcomes: dict[str, list[tuple[str, str]]] = {}
    for shard, data in sorted(shard_results.items()):
        seen_collect.update(data.get("collected", []))
        for node_id, outcome in data.get("outcomes", {}).items():
            outcomes.setdefault(node_id, []).append((shard, outcome))
        if data.get("exitstatus") not in (0,):
            problems.append(f"{shard}: pytest exit status {data.get('exitstatus')}")
    union = set(seen_collect)
    if missing := sorted(full - union):
        problems.append(f"{len(missing)} collected test(s) not collected by any shard, e.g. {missing[:5]}")
    if extra := sorted(union - full):
        problems.append(f"{len(extra)} test(s) collected by shards but absent from the full collection, e.g. {extra[:5]}")
    if dup := sorted(n for n, c in seen_collect.items() if c > 1):
        problems.append(f"{len(dup)} test(s) collected by more than one shard, e.g. {dup[:5]}")
    if not_run := sorted(full - set(outcomes)):
        problems.append(f"{len(not_run)} collected test(s) have no outcome (not executed), e.g. {not_run[:5]}")
    if twice := sorted(n for n, v in outcomes.items() if len(v) > 1):
        problems.append(f"{len(twice)} test(s) executed by more than one shard, e.g. {twice[:5]}")
    bad = Counter()
    examples: dict[str, list[str]] = {}
    for node_id, results in outcomes.items():
        for _, outcome in results:
            if outcome != "passed":
                bad[outcome] += 1
                examples.setdefault(outcome, []).append(node_id)
    for outcome, count in sorted(bad.items()):
        problems.append(f"{count} test(s) with outcome {outcome!r}, e.g. {examples[outcome][:5]}")
    if expected_count is not None and len(full) != expected_count:
        problems.append(f"full collection has {len(full)} tests, expected {expected_count}")
    return problems


def _cmd_collect(args) -> int:
    ids = collect_node_ids()
    # Shards collect again on their own runners: node ids must not depend on the run
    # (e.g. random values in parametrize without ids=), or the verification cannot match them.
    again = collect_node_ids()
    if ids != again:
        changed = sorted(set(ids) ^ set(again))
        raise SystemExit(f"non-deterministic test ids between two collections: {changed[:10]}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "collected.txt").write_text("\n".join(ids) + "\n", encoding="utf-8")
    shards = plan(ids, args.shards)
    per_file = Counter(i.split("::", 1)[0] for i in ids)
    for k, files in enumerate(shards, 1):
        print(f"shard {k}/{len(shards)}: {sum(per_file[f] for f in files)} tests in {len(files)} files")
    print(f"collected {len(ids)} tests in {len(per_file)} files")
    return 0


def _cmd_files(args) -> int:
    shards = plan(collect_node_ids(), args.shards)
    if not 1 <= args.shard <= len(shards):
        raise SystemExit(f"--shard must be in 1..{len(shards)}")
    print("\n".join(shards[args.shard - 1]))
    return 0


def _cmd_verify(args) -> int:
    collected = [line for line in Path(args.collected).read_text(encoding="utf-8").splitlines() if line]
    results = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(args.results).rglob("*.json"))}
    if not results:
        print("no shard result files found", file=sys.stderr)
        return 1
    problems = verify(collected, results, args.expected_count)
    total = sum(len(d.get("outcomes", {})) for d in results.values())
    print(f"shards: {len(results)}; collected: {len(collected)}; executed outcomes: {total}")
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"OK: every one of the {len(collected)} collected tests ran exactly once and passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("collect"); p.add_argument("--out", required=True); p.add_argument("--shards", type=int, required=True)
    p = sub.add_parser("files"); p.add_argument("--shard", type=int, required=True); p.add_argument("--shards", type=int, required=True)
    p = sub.add_parser("verify"); p.add_argument("--collected", required=True); p.add_argument("--results", required=True)
    p.add_argument("--expected-count", type=int)
    args = parser.parse_args(argv)
    return {"collect": _cmd_collect, "files": _cmd_files, "verify": _cmd_verify}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
