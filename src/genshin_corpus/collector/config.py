from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class CollectorConfig:
    base_url: str = "https://wiki.hoyolab.com"
    app_sn: str = "ys_obc"
    locale: str = "zh-cn"
    output_root: Path = Path("data/raw")
    run_id: str = ""
    timeout: float = 20.0
    max_retries: int = 3
    backoff_base: float = 0.25
    listing_params: Mapping[str, str] = field(default_factory=dict)

    def resolved_run_id(self) -> str:
        import datetime
        return self.run_id or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
