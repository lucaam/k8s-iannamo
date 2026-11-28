#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Check the data retention registry, .github/ci/retention.yaml.

  retention.py --check          validate the registry (pull request lint)
  retention.py --report FILE    write the entries past retainUntil as markdown
                                to FILE (empty file when there are none)
"""

import argparse
import datetime
import os
import sys
from pathlib import Path

import yaml

REGISTRY = Path(__file__).with_name("retention.yaml")
REQUIRED = ("app", "namespace", "since", "retainUntil", "reason", "data")
RUNBOOK = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
           f"{os.environ.get('GITHUB_REPOSITORY', 'lucaam/k8s-iannamo')}/blob/main/docs/retiring-an-app.en.md")


def load() -> tuple[list[dict], list[str]]:
    data = yaml.safe_load(REGISTRY.read_text()) or {}
    entries = data.get("retained") or []
    errors = []
    if not isinstance(entries, list):
        return [], ["'retained' must be a list"]
    for i, e in enumerate(entries):
        where = f"entry {i} ({e.get('app', '?') if isinstance(e, dict) else '?'})"
        if not isinstance(e, dict):
            errors.append(f"{where}: must be a map")
            continue
        errors += [f"{where}: missing '{k}'" for k in REQUIRED if k not in e]
        for k in ("since", "retainUntil"):
            if k in e and not isinstance(e[k], datetime.date):
                errors.append(f"{where}: '{k}' must be a date (YYYY-MM-DD)")
        if isinstance(e.get("since"), datetime.date) and isinstance(e.get("retainUntil"), datetime.date) \
                and e["retainUntil"] < e["since"]:
            errors.append(f"{where}: retainUntil is before since")
        if "data" in e and (not isinstance(e["data"], list) or not e["data"]):
            errors.append(f"{where}: 'data' must be a non-empty list")
    return entries, errors


def report(entries: list[dict], today: datetime.date) -> str:
    expired = [e for e in entries if e["retainUntil"] <= today]
    if not expired:
        return ""
    lines = [
        "The data below has passed its retention date. Nothing has been deleted.",
        "",
        "For each entry, either delete the data by hand, following "
        f"[docs/retiring-an-app.en.md]({RUNBOOK}), and then remove "
        "the entry from `.github/ci/retention.yaml`, or keep it on purpose by setting a new "
        "`retainUntil`.",
        "",
    ]
    for e in sorted(expired, key=lambda x: x["retainUntil"]):
        lines.append(f"- [ ] **{e['app']}** (namespace `{e['namespace']}`): retained since {e['since']}, "
                     f"until {e['retainUntil']}. {e['reason']}")
        lines += [f"  - {item}" for item in e["data"]]
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--report")
    args = p.parse_args()
    entries, errors = load()
    for e in errors:
        print(f"::error file={REGISTRY.as_posix()},title=retention::{e}")
    if errors:
        return 1
    if args.check:
        print(f"retention registry: {len(entries)} entries, valid")
        return 0
    text = report(entries, datetime.date.today())
    Path(args.report).write_text(text)
    print(f"retention: {text.count('- [ ]')} entries past their date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
