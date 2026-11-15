# Calibration cross validation and geographic safeguards

Training-only evaluation. Production remains unchanged. Models are fitted four times, each time excluding one training year. Every fold re-estimates its global and group ratios. No previously inspected final-test outcomes are used for this selection.

Eligible corrections need at least three fold wins, at least 1% relative mean-objective improvement, and no geographic sample mean WAPE increase greater than one percentage point. The objective balances national, pooled half-degree, and fixed-sample errors.

| Candidate | Mean objective | Fold wins | Geographic gate |
|---|---:|---:|---|
| global | 20.51 | 0/4 | Fewer than three of four held-training-year objective wins; Less than 1% relative mean-objective improvement |
| season_500 | 17.70 | 4/4 | Eligible for further evaluation |
| season_5000 | 18.13 | 4/4 | Eligible for further evaluation |
| tile_500 | 20.22 | 2/4 | Fewer than three of four held-training-year objective wins; Regional error guardrail: southeast |
| tile_5000 | 19.97 | 4/4 | Regional error guardrail: southeast |
| tile_quarter_500 | 16.84 | 4/4 | Regional error guardrail: southeast |
| tile_quarter_5000 | 18.14 | 4/4 | Regional error guardrail: southeast |
| tile_month_500 | 16.97 | 4/4 | Regional error guardrail: southeast |
| tile_month_5000 | 18.56 | 4/4 | Regional error guardrail: southeast |

Recommended for further evaluation: **season_500**.

This recommendation is not production approval. Retrospective folds share geographic locations, omit observation exposure, and have only four independent year blocks. The safeguards protect the fixed samples, not every small polygon or biome. Do not change an already-tested candidate and reuse 2024-2025 as if those outcomes were still unseen.

The current national model remains the deployed model. Missing prospective evidence and arbitrary-area uncertainty calibration are explicit blockers. No automatic downloads or retraining were added. Run `.venv/Scripts/python -m pipeline.calibration_gate` to inspect or reproduce the saved gate workflow.
