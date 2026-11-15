"""Configuration: site profiles and intervention scenarios (validated, typed settings).

Typical use:
    from beyond_the_wait.config import load_site_profile, load_scenario, apply_scenario

    site = load_site_profile("config/sites/fictional_bronx_community_ed.yaml")
    scenario = load_scenario("config/scenarios/add_evening_physician.yaml")
    changed_site = apply_scenario(site, scenario)
"""

from beyond_the_wait.config.errors import ConfigError, ScenarioError
from beyond_the_wait.config.loader import load_scenario, load_site_profile
from beyond_the_wait.config.models import (
    ArrivalAssumptions,
    InpatientCapacity,
    Scenario,
    Shift,
    SiteInfo,
    SiteProfile,
    StaffingChange,
    StaffingPlan,
    TreatmentSpaceChange,
    TreatmentSpaces,
)
from beyond_the_wait.config.scenarios import apply_scenario

__all__ = [
    "ArrivalAssumptions",
    "ConfigError",
    "InpatientCapacity",
    "Scenario",
    "ScenarioError",
    "Shift",
    "SiteInfo",
    "SiteProfile",
    "StaffingChange",
    "StaffingPlan",
    "TreatmentSpaceChange",
    "TreatmentSpaces",
    "apply_scenario",
    "load_scenario",
    "load_site_profile",
]
