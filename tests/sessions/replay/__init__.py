"""Modular replay consistency helpers.

The backend lifecycle remains in :mod:`tests.sessions.replay_harness`; this
package owns normalization, allowlist governance, comparison, reporting, and
raw-storage fault injection so each concern can be tested independently.
"""

from .allowed_diff import AllowedDiffRule
from .allowed_diff import MAX_ALLOWED_DIFF_RATIO
from .allowed_diff import MAX_ALLOWED_DIFF_RULES
from .allowed_diff import validate_allowed_diff_rules
from .comparator import diff_backend_snapshots
from .comparator import expected_diff_paths_for_backend_pair
from .normalizer import normalize_backend_snapshot
from .report import REPORT_SCHEMA_VERSION
from .report import build_acceptance_case_report
from .report import build_acceptance_criteria
from .report import build_acceptance_quality_metrics
from .report import build_case_matrix_report
from .report import build_comparison_report
from .report import write_diff_report

__all__ = [
    "AllowedDiffRule",
    "MAX_ALLOWED_DIFF_RATIO",
    "MAX_ALLOWED_DIFF_RULES",
    "REPORT_SCHEMA_VERSION",
    "build_acceptance_case_report",
    "build_acceptance_criteria",
    "build_acceptance_quality_metrics",
    "build_case_matrix_report",
    "build_comparison_report",
    "diff_backend_snapshots",
    "expected_diff_paths_for_backend_pair",
    "normalize_backend_snapshot",
    "validate_allowed_diff_rules",
    "write_diff_report",
]
