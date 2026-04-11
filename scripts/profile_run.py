"""Quick profiling wrapper for wikify_simple distill runs.

Records wall-clock start/end for each subprocess invocation plus a
summary from the resulting _calls.jsonl event log.
"""

import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path


def time_cmd(label: str, cmd: list[str]) -> dict:
    t0 = time.monotonic()
    rc = subprocess.run(cmd, check=False)
    wall = time.monotonic() - t0
    return {"label": label, "returncode": rc.returncode, "wall_seconds": wall}


def summarize_calls_jsonl(path: Path) -> dict:
    if not path.exists():
        return {"present": False}
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    by_role: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0, "wall_seconds": 0.0, "heq": 0.0}
    )
    total_heq = 0.0
    for row in rows:
        role = row.get("role", "unknown")
        by_role[role]["calls"] += 1
        by_role[role]["tokens_in"] += int(row.get("tokens_in", 0))
        by_role[role]["tokens_out"] += int(row.get("tokens_out", 0))
        by_role[role]["wall_seconds"] += float(row.get("wall_seconds", 0.0))
        by_role[role]["heq"] += float(row.get("haiku_equivalent", 0.0))
        total_heq += float(row.get("haiku_equivalent", 0.0))
    return {
        "present": True,
        "total_calls": len(rows),
        "total_heq": round(total_heq, 1),
        "by_role": {k: {**v, "heq": round(v["heq"], 1), "wall_seconds": round(v["wall_seconds"], 3)} for k, v in by_role.items()},
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: profile_run.py <plan.json>", file=sys.stderr)
        return 2
    plan_path = Path(sys.argv[1])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    results: list[dict] = []
    start = time.monotonic()
    for step in plan["steps"]:
        label = step["label"]
        cmd = step["cmd"]
        print(f"\n=== {label} ===")
        print("  $", " ".join(cmd))
        r = time_cmd(label, cmd)
        print(f"  wall: {r['wall_seconds']:.2f}s  rc={r['returncode']}")
        results.append(r)
        if r["returncode"] != 0 and step.get("fail_fast", True):
            break
    total_wall = time.monotonic() - start

    bundle = Path(plan.get("bundle", ""))
    calls_summary = summarize_calls_jsonl(bundle / "_calls.jsonl") if bundle.name else {}
    run_snapshot = None
    if bundle.name and (bundle / "_run.json").exists():
        run_snapshot = json.loads((bundle / "_run.json").read_text(encoding="utf-8"))

    report = {
        "plan": str(plan_path),
        "total_wall_seconds": round(total_wall, 2),
        "steps": results,
        "calls_summary": calls_summary,
        "run_snapshot_keys": list(run_snapshot.keys()) if run_snapshot else [],
        "n_pages": run_snapshot.get("n_pages") if run_snapshot else None,
        "budget_used_heq": run_snapshot.get("budget_used_haiku_eq") if run_snapshot else None,
    }
    out_path = plan_path.with_suffix(".report.json")
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n=== report: {out_path}")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
