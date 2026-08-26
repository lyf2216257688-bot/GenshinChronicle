"""MiHoYo OBC source adapter."""

from .adapter import OBCDetailParser, parse_obc_detail
from .pipeline import OBCParsedRunPipeline, ParseDependencies

__all__ = ["OBCDetailParser", "parse_obc_detail", "OBCParsedRunPipeline", "ParseDependencies"]
