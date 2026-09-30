"""ICT PD Array Matrix: multi-timeframe PD array detection with a full lifecycle."""

from .engine import TFEngine, step
from .matrix import MatrixEngine
from .model import (
    DISCOUNT_ORDER,
    KIND_NAME,
    PREMIUM_ORDER,
    Candle,
    Config,
    Dir,
    Event,
    Kind,
    PDArray,
    SetupConfig,
    State,
    Trade,
    discount_rank,
    premium_rank,
)
from .setup import SetupModel
from .timeframes import Aggregator, bucket_key, in_macro, parse_macros, tf_name, tf_seconds

__all__ = [
    "Aggregator",
    "Candle",
    "Config",
    "DISCOUNT_ORDER",
    "Dir",
    "Event",
    "KIND_NAME",
    "Kind",
    "MatrixEngine",
    "PDArray",
    "PREMIUM_ORDER",
    "SetupConfig",
    "SetupModel",
    "State",
    "TFEngine",
    "Trade",
    "bucket_key",
    "discount_rank",
    "in_macro",
    "parse_macros",
    "premium_rank",
    "step",
    "tf_name",
    "tf_seconds",
]
