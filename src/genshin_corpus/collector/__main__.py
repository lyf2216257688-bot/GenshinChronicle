import argparse
import json
from pathlib import Path

from .access import SourceAccess
from .collector import Collector
from .config import DEFAULT_BASE_URL, CollectorConfig


def _non_negative_int(value):
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def main(argv=None, *, access=None, transport=None) -> int:
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
    parser.add_argument("--acknowledge-access", metavar="EVENT_ID",
                        help="acknowledge verified restoration after a 403 or interrupted dispatch; make no requests")
    parser.add_argument("--reason", help="reason for acknowledging access restoration")
    args = parser.parse_args(argv)
    if args.acknowledge_access is not None:
        if (args.details or args.channel_ids is not None or args.detail_limit is not None or args.run_id
                or args.base_url != DEFAULT_BASE_URL or args.locale != "zh-cn" or args.output_root != Path("data/raw")
                or args.timeout != 20.0 or args.max_retries != 3):
            parser.error("access acknowledgment is a standalone operation")
        try:
            state = access if access is not None else SourceAccess()
            receipt = state.acknowledge(args.acknowledge_access, args.reason)
        except (OSError, ValueError) as exc:
            print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps({"status": "access_acknowledged", "event_id": receipt["event"]["event_id"]}))
        return 0
    if args.reason is not None:
        parser.error("--reason requires --acknowledge-access")
    try:
        collector = Collector(CollectorConfig(
            base_url=args.base_url, locale=args.locale, output_root=args.output_root, run_id=args.run_id,
            timeout=args.timeout, max_retries=args.max_retries,
        ), transport=transport, access=access)
        manifest = collector.run(fetch_details=args.details, channel_ids=args.channel_ids, detail_limit=args.detail_limit)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 2
    partial = manifest["status"] == "partial"
    summary = {
        "run_id": manifest["run_id"], "status": manifest["status"], "request_scope": manifest["request_scope"],
        "full_coverage": manifest["full_coverage"], "coverage": manifest["coverage"], "failures": manifest["failures"],
        "http_responses_saved": manifest["http_responses_saved"],
        "successful_detail_fetches": manifest["successful_detail_fetches"],
        "failed_detail_fetches": manifest["failed_detail_fetches"],
        "pending_detail_count": len(manifest["detail_selection"]["selected_content_ids"])
                                - manifest["successful_detail_fetches"],
        "manifest_path": str(collector.store.checkpoint_path if partial else collector.store.manifest_path),
    }
    if "access_restriction" in manifest:
        summary["access_restriction"] = manifest["access_restriction"]
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
