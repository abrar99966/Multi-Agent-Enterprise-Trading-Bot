"""CLI: verify hash-chained audit journal(s).

Usage:
    python scripts/verify_audit_chain.py <file-or-directory> [--glob PATTERN]

Prints one PASS/FAIL line per journal with the record count and, on
failure, the first bad line and reason. Exits 0 if every file verifies,
1 if any fails (argparse exits 2 on bad arguments, e.g. a missing path).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.audit.chain import VerifyReport, verify_directory, verify_journal  # noqa: E402


def _format(name: str, report: VerifyReport) -> str:
    parts = [
        "PASS" if report.ok else "FAIL",
        name,
        f"records={report.records}",
    ]
    if report.first_bad_line is not None:
        parts.append(f"first_bad_line={report.first_bad_line}")
    if report.reason is not None:
        parts.append(f"reason={report.reason}")
    return "  ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the tamper-evident hash chain of audit journal(s)."
    )
    parser.add_argument(
        "path",
        type=Path,
        help="journal .jsonl file, or a directory of journals",
    )
    parser.add_argument(
        "--glob",
        default="*.jsonl",
        help="filename pattern when PATH is a directory (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    path: Path = args.path
    if path.is_dir():
        reports = verify_directory(path, args.glob)
        if not reports:
            print(f"no files matching {args.glob!r} under {path}")
            return 0
    elif path.is_file():
        reports = {str(path): verify_journal(path)}
    else:
        parser.error(f"no such file or directory: {path}")

    for name, report in reports.items():
        print(_format(name, report))
    return 0 if all(r.ok for r in reports.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
