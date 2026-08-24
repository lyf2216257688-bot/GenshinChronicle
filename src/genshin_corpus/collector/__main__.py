import argparse
from pathlib import Path

from .collector import Collector
from .config import DEFAULT_BASE_URL, CollectorConfig


def _non_negative_int(value):
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Collect mihoyo_obc Raw responses")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--locale", default="zh-cn")
    parser.add_argument("--output-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--channel-id", action="append", dest="channel_ids", metavar="CHANNEL_ID",
                        help="limit listings to this channel; repeat for multiple channels")
    parser.add_argument("--detail-limit", type=_non_negative_int, default=None, metavar="N",
                        help="limit detail requests to at most N unique content IDs")
    parser.add_argument("--details", action="store_true", help="fetch detail responses (opt-in)")
    args = parser.parse_args(argv)
    run_options = {"fetch_details": args.details}
    if args.channel_ids is not None:
        run_options["channel_ids"] = args.channel_ids
    if args.detail_limit is not None:
        run_options["detail_limit"] = args.detail_limit
    Collector(CollectorConfig(base_url=args.base_url, locale=args.locale, output_root=args.output_root,
                              run_id=args.run_id, timeout=args.timeout, max_retries=args.max_retries)).run(
                                  **run_options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
