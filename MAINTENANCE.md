# Maintenance runbook

Expected effort: **2 minutes a week, 15 minutes a month, about 2 hours each May.**
The forecast archive runs and heals itself. Your job is to notice when it can't.

All commands run from the repo folder with the virtual environment active:

```bash
cd ~/Desktop/"Tirth Private"/ew-energy-models
source .venv/bin/activate
git pull            # always first: the archive bot commits to main twice a day
```

---

## One-time setup

1. **Push the repo** to `github.com/enerwyse/ew-energy-models` (public).
2. **Allow the bot to commit:** GitHub → repo **Settings → Actions → General →
   Workflow permissions → "Read and write permissions"** → Save.
3. **Turn on failure emails:** GitHub (your avatar) → **Settings → Notifications →
   Actions → "Send notifications for failed workflows only"**, with email enabled.
4. **First archive run:** repo → **Actions → archive-ieso-forecast → Run workflow**.
   It should finish green and commit `data/ieso_forecast_archive.csv`.
5. **Reserve the PyPI name** (5 min): create an account at pypi.org, then
   `pip install build twine && python -m build && twine upload dist/*`.
6. Add a calendar reminder: **first Monday of each month, "ew-models check"**, and
   **May 15, "ICI season update"**.

## Weekly (2 minutes)

- Glance at your email for an `archive-ieso-forecast` failure. **No email = healthy.**
- Optional: repo → Actions tab. Green ticks on recent runs = fine.

## When a failure email arrives

The failure tells you which step broke. Open the run in the **Actions** tab.

| Failed step | Meaning | What to do |
|---|---|---|
| **Gap check** | Recent dates are missing a day-ahead forecast | Click **Re-run all jobs**. Missing dates are recoverable for about 40 days, so you have time, but don't leave it. |
| **Back-fill** with an HTTP / timeout error | IESO site was down | Ignore it once. The next run heals it. If it fails for 3+ days, investigate. |
| **Back-fill** with `no ForecastOntDemand/AverageDemand series found` or a parse error | **IESO changed its report format** | See "IESO changed a format" below. |
| **Commit** with a permission error | Bot can't push | Redo one-time setup step 2. |

If anything fails for more than a week, open a Claude session in the Enerwyse
project, paste the error, and ask for a fix.

## Monthly (15 minutes, first Monday)

1. `git pull`
2. Check archive health:
   ```bash
   python scripts/archive_ieso_forecast.py --check-only
   ```
   You want `OK: ...`. "Known permanent gaps" are fine; IESO had already deleted those.
3. Run the tests: `pytest -q`. All should pass. Warnings about deprecations are
   a to-do, not an emergency.
4. Check GitHub **Issues**. Reply to anything new within a week, even just
   "thanks, looking at it."
5. GitHub pauses scheduled workflows after 60 days with no repo activity. The bot's
   commits normally prevent this. If the Actions tab shows a yellow "disabled"
   banner, click **Enable workflow**.

## Yearly: ICI season update (May 15–31)

The base period closes April 30. IESO publishes the final ICI demand file in early May.

1. `git pull`
2. Run the full analysis:
   ```bash
   python examples/ontario_peak_atlas.py | tee out/run_output.txt
   ```
3. Check section 1: **Matching peak hours should be 25/25** (or 5 more each year).
   If any mismatch appears, stop and investigate before publishing anything.
4. Note the new season's peak date, threshold, crowding and window coverage.
5. Once a full summer of the forecast archive exists (first time: May 2027), run the
   benchmark on real IESO forecasts (ask Claude to wire it in).
6. Release a version (below) and publish the "ICI season in review" post.
7. Re-read IESO's ICI page and the Class A eligibility rules for changes
   (thresholds, PPA offsets, base-period dates). Update `ontario/ici.py` caveats if needed.

## Releasing a new version

1. Update the version in **three places**: `pyproject.toml`, `src/ewmodels/__init__.py`,
   `CITATION.cff` (plus `date-released`).
2. `pytest -q` must pass.
3. Commit, then tag:
   ```bash
   git commit -am "vX.Y.Z: <what changed>"
   git tag vX.Y.Z && git push && git push --tags
   ```
4. Publish: `rm -rf dist && python -m build && twine upload dist/*`
5. GitHub → **Releases → Draft a new release** from the tag, with 3–5 lines on what's new.

## IESO changed a format

Signs: parse errors, missing columns, or the workflow failing every run.

1. Look at a current file in the browser:
   `https://reports-public.ieso.ca/public/Adequacy3/` or `.../Demand/`
2. For forecasts, run `python -m ewmodels.data.ieso_forecast --inspect` and see
   which series it finds.
3. Ask Claude with the error and the inspect output. Fix, add a test, release.
4. **Time matters for the forecast archive:** versions disappear after about 45 days.

## Rules that never change

- **No customer data in this repo, ever.** That covers meter data, Green Button
  files, and anything from the Enerwyse platform, anonymized or not. `.gitignore`
  blocks XML, but the rule is yours to keep.
- Every new model returns a `Result` with a receipt: checks plus caveats.
- Every change comes with a test. Tests must pass before pushing.
- Charts use fixed axes (never let autoscale exaggerate a small difference).
- Anything published publicly gets verified against an official source first.
