"""The coverage engine. Views, commands and tests import from here only."""

from coverage.services.demand import build_demand
from coverage.services.gaps import detect_gaps
from coverage.services.requirements import demand_for, gap_for, refresh_requirement, window_gaps

__all__ = ["build_demand", "detect_gaps", "demand_for", "gap_for", "refresh_requirement", "window_gaps"]
