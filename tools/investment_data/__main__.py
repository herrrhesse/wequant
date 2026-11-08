"""python -m tools.investment_data --tag latest"""

import argparse
import sys
from pathlib import Path

from tools.investment_data.run import run_import


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a chenditc/investment_data release into the wequant store.")
    parser.add_argument("--tag", default="latest", help='release tag, or "latest"')
    parser.add_argument("--home", type=Path, help="data root (default: $WEQUANT_HOME or ~/.wequant)")
    parser.add_argument("--skip-idempotency-check", action="store_true", help="do not re-import to check idempotency")
    args = parser.parse_args(argv)
    code, report = run_import(args.tag, args.home, check_idempotency=not args.skip_idempotency_check)
    print(f"{'imported' if code == 0 else 'STOPPED'}; report: {report}")
    return code


if __name__ == "__main__":
    sys.exit(main())
