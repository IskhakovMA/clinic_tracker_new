"""Split the backend test suite into CI shards and prove that all of it ran.

    collect      --shards N [--out DIR]
        Collect every test twice (node ids must be deterministic), check that the
        shard plan partitions all test files, print the count and SHA-256 digest of
        the collection and, on GitHub Actions, export them as job outputs.
    files        --shard K --shards N [--expect-count C --expect-digest D] [--planned-out FILE]
        Collect again on this runner, require the same count and digest as the plan
        job (identical collection), write the node ids planned for shard K and print
        its test files.
    verify-shard --planned FILE --outcomes FILE
        Fail unless this shard collected exactly its planned tests and every one of
        them has exactly one outcome, `passed` (no failures, errors, skips, misses).
    verify       --collected FILE --results DIR [--expected-count N]
        Whole-suite check over several shard outcome files (local runs).

Sharding is by test file, which keeps a file's module-level state and fixtures
together. Files are assigned deterministically by test count (largest first,
each to the currently smallest shard; ties by path), so every shard job derives
the same plan from the same collection. No test is skipped, deselected or
ignored: a shard runs `pytest <its files>`.

Proof of completeness without shared storage: the plan job proves the shards'
files partition the collection; every shard proves it ran on the identical
collection (count + digest) exactly the tests planned for it, all passed; the
verify job requires the plan job and every shard to succeed. Together: every
collected test ran exactly once and passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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


def digest(node_ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(node_ids)).encode("utf-8")).hexdigest()


def check_partition(node_ids: list[str], shards: list[list[str]]) -> list[str]:
    files = [f for bucket in shards for f in bucket]
    expected = {node_id.split("::", 1)[0] for node_id in node_ids}
    problems = []
    if len(files) != len(set(files)):
        problems.append("a test file is planned for more than one shard")
    if set(files) != expected:
        problems.append(f"plan does not cover exactly the collected files: missing {sorted(expected - set(files))[:5]}, "
                        f"extra {sorted(set(files) - expected)[:5]}")
    if any(not bucket for bucket in shards):
        problems.append("a shard has no test files")
    return problems


def _dups(ids) -> list[str]:
    return sorted(n for n, c in Counter(ids).items() if c > 1)


def verify_shard(planned: list[str], result: dict) -> list[str]:
    """One shard, strict set equality: planned == collected == outcome keys == run keys;
    no duplicates; every test executed exactly once with outcome `passed`; exit status 0."""
    problems = []
    collected = list(result.get("collected", []))
    outcomes = result.get("outcomes", {})
    runs = result.get("runs")
    if not planned:
        problems.append("shard has no planned tests")
    if dup := _dups(planned):
        problems.append(f"{len(dup)} duplicate id(s) in the shard plan, e.g. {dup[:5]}")
    if dup := _dups(collected):
        problems.append(f"{len(dup)} test(s) collected more than once by the shard, e.g. {dup[:5]}")
    plan_set, coll_set, out_set = set(planned), set(collected), set(outcomes)
    if missing := sorted(plan_set - coll_set):
        problems.append(f"{len(missing)} planned test(s) not collected by the shard, e.g. {missing[:5]}")
    if extra := sorted(coll_set - plan_set):
        problems.append(f"{len(extra)} test(s) collected by the shard but not planned for it, e.g. {extra[:5]}")
    if not_run := sorted(plan_set - out_set):
        problems.append(f"{len(not_run)} planned test(s) have no outcome (not executed), e.g. {not_run[:5]}")
    if foreign := sorted(out_set - plan_set):
        problems.append(f"{len(foreign)} outcome(s) for tests that are not planned for this shard, e.g. {foreign[:5]}")
    if runs is None:
        problems.append("outcome file has no execution counts ('runs'): outdated plugin")
    else:
        if set(runs) != plan_set:
            problems.append(f"execution counts do not match the plan: missing {sorted(plan_set - set(runs))[:5]}, "
                            f"foreign {sorted(set(runs) - plan_set)[:5]}")
        if repeated := sorted(n for n, c in runs.items() if c != 1):
            problems.append(f"{len(repeated)} test(s) not executed exactly once, e.g. "
                            f"{[(n, runs[n]) for n in repeated[:5]]}")
    bad: dict[str, list[str]] = {}
    for node_id, outcome in outcomes.items():
        if outcome != "passed":
            bad.setdefault(outcome, []).append(node_id)
    for outcome, ids in sorted(bad.items()):
        problems.append(f"{len(ids)} test(s) with outcome {outcome!r}, e.g. {ids[:5]}")
    if result.get("exitstatus") != 0 or isinstance(result.get("exitstatus"), bool):
        problems.append(f"pytest exit status {result.get('exitstatus')!r}, expected 0")
    return problems


def _github_output(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")


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
    if foreign := sorted(set(outcomes) - full):
        problems.append(f"{len(foreign)} outcome(s) for tests absent from the full collection, e.g. {foreign[:5]}")
    run_counts: Counter = Counter()
    for shard, data in shard_results.items():
        if "runs" not in data:
            problems.append(f"{shard}: outcome file has no execution counts ('runs')")
        run_counts.update(data.get("runs", {}))
    if repeated := sorted(n for n, c in run_counts.items() if c != 1):
        problems.append(f"{len(repeated)} test(s) not executed exactly once, e.g. {[(n, run_counts[n]) for n in repeated[:5]]}")
    if never := sorted(full - set(run_counts)):
        problems.append(f"{len(never)} collected test(s) without an execution count, e.g. {never[:5]}")
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
    shards = plan(ids, args.shards)
    if problems := check_partition(ids, shards):
        raise SystemExit("; ".join(problems))
    per_file = Counter(i.split("::", 1)[0] for i in ids)
    for k, files in enumerate(shards, 1):
        print(f"shard {k}/{len(shards)}: {sum(per_file[f] for f in files)} tests in {len(files)} files")
    value = digest(ids)
    print(f"collected {len(ids)} tests in {len(per_file)} files; sha256 {value}")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "collected.txt").write_text("\n".join(ids) + "\n", encoding="utf-8")
    _github_output(count=len(ids), digest=value)
    return 0


def _cmd_files(args) -> int:
    ids = collect_node_ids()
    if args.expect_count is not None and len(ids) != args.expect_count:
        raise SystemExit(f"collection differs from the plan job: {len(ids)} tests, expected {args.expect_count}")
    if args.expect_digest and digest(ids) != args.expect_digest:
        raise SystemExit("collection differs from the plan job: digest mismatch")
    shards = plan(ids, args.shards)
    if not 1 <= args.shard <= len(shards):
        raise SystemExit(f"--shard must be in 1..{len(shards)}")
    files = shards[args.shard - 1]
    if args.planned_out:
        chosen = set(files)
        planned = [i for i in ids if i.split("::", 1)[0] in chosen]
        Path(args.planned_out).write_text("\n".join(planned) + "\n", encoding="utf-8")
    print("\n".join(files))
    return 0


def _cmd_verify_shard(args) -> int:
    planned = [line for line in Path(args.planned).read_text(encoding="utf-8").splitlines() if line]
    result = json.loads(Path(args.outcomes).read_text(encoding="utf-8"))
    problems = verify_shard(planned, result)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems or not planned:
        return 1
    print(f"OK: this shard ran exactly its {len(planned)} planned tests once each, all passed")
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
    p = sub.add_parser("collect"); p.add_argument("--out"); p.add_argument("--shards", type=int, required=True)
    p = sub.add_parser("files"); p.add_argument("--shard", type=int, required=True); p.add_argument("--shards", type=int, required=True)
    p.add_argument("--expect-count", type=int); p.add_argument("--expect-digest"); p.add_argument("--planned-out")
    p = sub.add_parser("verify-shard"); p.add_argument("--planned", required=True); p.add_argument("--outcomes", required=True)
    p = sub.add_parser("verify"); p.add_argument("--collected", required=True); p.add_argument("--results", required=True)
    p.add_argument("--expected-count", type=int)
    args = parser.parse_args(argv)
    return {"collect": _cmd_collect, "files": _cmd_files, "verify-shard": _cmd_verify_shard,
            "verify": _cmd_verify}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
