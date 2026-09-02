"""Rebuild the bundled "stops at" seed matrix from the live RTPI API.

Thin CLI wrapper around
:func:`custom_components.irish_rail.matrix_rebuild.sample_stops_matrix`.

    python scripts/build_stops_matrix.py            # full rebuild
    python scripts/build_stops_matrix.py --limit 5  # smoke test
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import aiohttp

_REPO_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(_REPO_ROOT))

from custom_components.irish_rail.client import IrishRailClient
from custom_components.irish_rail.matrix_rebuild import sample_stops_matrix

_LOGGER = logging.getLogger(__name__)
DEFAULT_OUTPUT = (
    _REPO_ROOT / "custom_components" / "irish_rail" / "stops_matrix.seed.json"
)


def main() -> None:
    """Parse arguments and run the sampler."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    async def _run() -> None:
        async with aiohttp.ClientSession() as session:
            client = IrishRailClient(session)
            result = await sample_stops_matrix(
                client,
                gap_fill=False,
                atomic_dump=True,
                priority="normal",
                delay=args.delay,
                limit=args.limit,
                output_path=args.output,
            )
            if result.error:
                _LOGGER.error("Rebuild failed: %s", result.error)
            else:
                _LOGGER.info("Wrote %d stations to %s", result.sampled, args.output)

    asyncio.run(_run())

if __name__ == "__main__":
    main()
