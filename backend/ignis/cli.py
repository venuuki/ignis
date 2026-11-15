from __future__ import annotations
import argparse
import os
from datetime import date
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from .firms import STUDY_BBOX, SOURCES, fetch_range
from .compare import daily_comparison


def main():
    parser = argparse.ArgumentParser(description="IGNIS Phase 1: FIRMS raw sensor comparison")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--offline", action="store_true", help="Use cached files only")
    parser.add_argument("--bbox", nargs=4, type=float, default=STUDY_BBOX,
                        metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--modis-source", default=SOURCES["modis"])
    parser.add_argument("--viirs-source", default=SOURCES["viirs"])
    args = parser.parse_args()
    load_dotenv()
    cache = Path("data/raw")
    output = Path("outputs")
    output.mkdir(exist_ok=True)
    modis = fetch_range(args.modis_source, args.bbox, args.start, args.end, cache, offline=args.offline)
    viirs = fetch_range(args.viirs_source, args.bbox, args.start, args.end, cache, offline=args.offline)
    modis.to_csv(output / "modis_observations.csv", index=False)
    viirs.to_csv(output / "viirs_observations.csv", index=False)
    comparison = daily_comparison(modis, viirs, args.start, args.end)
    comparison.to_csv(output / "daily_comparison.csv", index=False)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(comparison["date"], comparison["modis_detections"], label="MODIS (raw)")
    ax.plot(comparison["date"], comparison["viirs_detections"], label="VIIRS (raw)")
    ax.set_ylabel("Hotspot records (not number of fires)")
    ax.set_title("Southeastern Australia: raw FIRMS detections (not harmonized)")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output / "raw_comparison.png", dpi=160)
    plt.close(fig)
    print(f"MODIS records: {len(modis)} | VIIRS records: {len(viirs)}")
    print(f"Saved outputs to {output.resolve()}")
    print("WARNING: Counts are unharmonized; zero records do not prove no fire.")


if __name__ == "__main__":
    main()
