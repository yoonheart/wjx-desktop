"""答案生成层公开接口。"""

from wjxdesk.engine.constraints import RULES, Violation, check, repair
from wjxdesk.engine.controller import LatentController, build_matrix, evaluate
from wjxdesk.engine.diagnostics import QualityReport, cronbach_alpha, kmo, report
from wjxdesk.engine.quota import QuotaBalancer
from wjxdesk.engine.rng import derive
from wjxdesk.engine.sampling import generate_sheet, shape_weights
from wjxdesk.engine.unique_pool import UniquePool

__all__ = [
    "RULES",
    "LatentController",
    "QualityReport",
    "QuotaBalancer",
    "UniquePool",
    "Violation",
    "build_matrix",
    "check",
    "cronbach_alpha",
    "derive",
    "evaluate",
    "generate_sheet",
    "kmo",
    "repair",
    "report",
    "shape_weights",
]
