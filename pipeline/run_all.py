"""Run the complete EmberCal data pipeline:  python -m pipeline.run_all"""
import sys

from pipeline import drivers, forecast, harmonize_v3 as harmonize, ingest_firms
from pipeline.common import load_env

if __name__ == "__main__":
    load_env()
    for step in (ingest_firms, harmonize, drivers, forecast):
        print(f"===== {step.__name__} =====", flush=True)
        sys.argv = [step.__name__]
        step.main()
