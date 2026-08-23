import argparse
from pathlib import Path

from .collector import Collector
from .config import CollectorConfig


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Collect mihoyo_obc Raw responses")
    parser.add_argument("--base-url", default="https://wiki.hoyolab.com")
    parser.add_argument("--locale", default="zh-cn")
    parser.add_argument("--output-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--details", action="store_true", help="fetch detail responses (opt-in)")
    args = parser.parse_args(argv)
    Collector(CollectorConfig(base_url=args.base_url, locale=args.locale, output_root=args.output_root,
                              run_id=args.run_id, timeout=args.timeout, max_retries=args.max_retries)).run(
                                  fetch_details=args.details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
