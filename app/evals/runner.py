"""Offline Braintrust experiment runner for the Google Ads analyst.

Scores all rows in the "ads-analyst-turns" dataset against the four scorers
and logs a named experiment to Braintrust so prompt versions can be compared.

Run:
    cd prod && uv run python -m app.evals.runner

Optional flags:
    --dataset   Name of the Braintrust dataset  (default: ads-analyst-turns)
    --exp       Experiment name override         (default: <PROMPT_VERSION>-<today>)
    --limit     Max rows to score               (default: all)
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from loguru import logger

from app.agent.prompts import PROMPT_VERSION
from app.config import settings
from app.evals.scorers import (
    accuracy_scorer,
    date_range_scorer,
    metric_compliance_scorer,
    no_recommendation_scorer,
)


async def _score_row(row: dict) -> dict[str, float]:
    reply: str = row.get("output") or ""
    metadata: dict = row.get("metadata") or {}
    fact_pack_deltas: dict = metadata.get("fact_pack_deltas") or {}

    return {
        "accuracy": accuracy_scorer(reply, fact_pack_deltas),
        "no_recommendations": await no_recommendation_scorer(reply),
        "metric_compliance": metric_compliance_scorer(reply, row),
        "date_range": date_range_scorer(reply),
    }


async def run_experiment(
    dataset_name: str = "ads-analyst-turns",
    experiment_name: str | None = None,
    limit: int | None = None,
) -> None:
    if not settings.braintrust_api_key:
        logger.error("BRAINTRUST_API_KEY not set — aborting")
        return

    try:
        import braintrust
    except ImportError:
        logger.error("braintrust not installed — run: uv add braintrust")
        return

    exp_name = experiment_name or f"{PROMPT_VERSION}-{date.today()}"
    logger.info(f"Loading dataset '{dataset_name}' from project '{settings.braintrust_project}'")

    dataset = braintrust.load_dataset(
        project=settings.braintrust_project,
        name=dataset_name,
    )
    rows = list(dataset)
    if limit:
        rows = rows[:limit]

    logger.info(f"Scoring {len(rows)} rows → experiment '{exp_name}'")

    experiment = braintrust.init(
        project=settings.braintrust_project,
        experiment=exp_name,
    )

    for i, row in enumerate(rows):
        try:
            scores = await _score_row(row)
        except Exception as exc:
            logger.warning(f"Row {i} scoring failed: {exc}")
            scores = {}

        experiment.log(
            input=row.get("input"),
            output=row.get("output"),
            scores=scores,
            metadata=row.get("metadata"),
        )
        logger.debug(f"Row {i + 1}/{len(rows)} scored: {scores}")

    experiment.flush()
    logger.info("Experiment complete — open Braintrust to compare prompt versions")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Braintrust eval experiment")
    parser.add_argument("--dataset", default="ads-analyst-turns")
    parser.add_argument("--exp", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    asyncio.run(
        run_experiment(
            dataset_name=args.dataset,
            experiment_name=args.exp,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
