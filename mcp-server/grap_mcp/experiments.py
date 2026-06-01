"""Experiment helpers + the production-only quality view (quarantine boundary).

Experiments are runs with kind="experiment" and a hypothesis recorded in the run
summary. quality_metrics aggregates ONLY production runs, so experiment runs can
never leak into production quality reporting.
"""

from typing import Any, Dict, Optional


def experiment_summary(scope: str, hypothesis: Optional[str]) -> Dict[str, str]:
    """Build the run-summary map for an experiment run."""
    summary = {"scope": scope, "experiment": "true"}
    if hypothesis:
        summary["hypothesis"] = hypothesis
    return summary


def quality_metrics(ws) -> Dict[str, Any]:
    """Production-only quality view. Experiments are reported only as a count.

    The latest *completed* production run is found via the chronological
    `run_completed` event stream, because run records carry no timestamp.
    """
    prod = {r.run_id: r for r in ws.list_runs("production")}
    experiments_excluded = len(ws.list_runs("experiment"))

    completed = [e for e in ws.query_events("run_completed") if e.subject in prod]
    last = completed[-1].subject if completed else None
    latest_gaps_found = prod[last].gaps_found if last else None

    return {
        "production_runs": len(prod),
        "production_completed": len(completed),
        "last_production_run": last,
        "latest_gaps_found": latest_gaps_found,
        "experiments_excluded": experiments_excluded,
    }
