# IGR Property Analytics Pipeline

A Python pipeline that loads real estate transaction data from PostgreSQL, preprocesses it, and produces aggregated Excel reports across three levels — **Project**, **Location**, and **City** — each in Overall / YoY / QoQ variants.

---

## Table of Contents

- [Overview](#overview)
- [Project Structure](#project-structure)
- [Setup](#setup)
- [Running the Pipeline](#running-the-pipeline)
- [Pipelines](#pipelines)
- [Configuration](#configuration)
- [Stats Engines](#stats-engines)
- [Output Column Naming](#output-column-naming)
- [Known Issues](#known-issues)
- [Adding a New City](#adding-a-new-city)

---

## Overview

| Item | Detail |
|------|--------|
| Language | Python 3.9+ |
| Database | PostgreSQL (SQLAlchemy + psycopg2) |
| Output | `.xlsx` / `.csv` per pipeline category |
| Entry Point | `run.py` |
| Config | `config.py` — all constants and per-city overrides |

---

## Project Structure

```
project_root/
├── run.py                       # Entry point — DB connection, city selection, pipeline orchestration
├── config.py                    # All constants: loading factors, ranges, DB config, per-city overrides
├── preprocessing.py             # Raw DataFrame → clean, analysis-ready DataFrame
├── aggregators/
│   ├── base.py                  # Shared masks, pivot helpers, range processors
│   ├── project.py               # Project-level aggregation (Overall / YoY / QoQ)
│   ├── location.py              # Location-level aggregation (Overall / YoY / QoQ)
│   └── city.py                  # City-level aggregation (Overall / YoY / QoQ)
└── stats/
    ├── area.py                  # Area-range statistics engine
    ├── price.py                 # Price-range statistics with INR formatting (Cr / L / K)
    ├── rate.py                  # Rate percentiles, prevailing range, floor-wise 90P rate
    ├── age.py                   # Buyer age-range statistics
    └── buyer.py                 # Pincode / buyer origin statistics
```

---

## Setup

### 1. Install dependencies

```bash
pip install pandas numpy sqlalchemy psycopg2-binary openpyxl rapidfuzz python-dotenv
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=your_database
DB_USER=your_user
DB_PASSWORD=your_password
```

### 3. Reference Excel files

Two Excel files must exist at the paths configured at the top of `run.py`:

| File | Required Columns |
|------|-----------------|
| `RERA_All_Keywords_BHK_Prop_Type.xlsx` | `BHK`, `standard_label` |
| `Property_type_keywords.xlsx` | `property_type_raw`, `property_type_refined` |

### 4. Database schema

The pipeline expects two tables:

- **`city`** — columns: `city_id`, `city_name`
- **`property_transaction_db1`** — transaction records with a `city_id` foreign key

Table names are configurable via `DB_CITIES_TABLE` and `DB_TRANSACTIONS_TABLE` in `config.py`.

---

## Running the Pipeline

```bash
python run.py
```

The script will:

1. Connect to PostgreSQL and list available cities
2. Prompt you to select which cities to process (press Enter for all)
3. Load, preprocess, and map all transaction data
4. Run all 9 pipelines per city and accumulate results
5. Save three merged output files to `OUTPUT_DIR`

> [!NOTE]
> **Robust Saving Logic**: The script automatically attempts to delete existing output files before saving to ensure a clean state. If an output file is open in Excel (causing a `PermissionError`), the script will alert you and automatically save a backup with a timestamp (e.g., `city_merged_20260312_120000.xlsx`) so your progress is never lost.

> If a result exceeds Excel's 16,384 column limit it is automatically saved as `.csv` instead.

---

## Pipelines

Each pipeline runs independently per city, then all city results are stacked via `pd.concat` (missing columns become `NaN`). BHK/br columns are reordered numerically (e.g., `<1 br`, `1 BHK`, `1.5 br`, `2 BHK`, `>3 br`, `4 BHK`) in the final output.

| Pipeline | Group Keys | Bin Strategy | Output File |
|----------|-----------|--------------|-------------|
| Project (Overall) | `proj_id` | mean | `project_merged.xlsx` |
| Project YoY | `proj_id` + `year` | mean | `project_merged.xlsx` |
| Project QoQ | `proj_id` + `quarter` | mean | `project_merged.xlsx` |
| Location (Overall) | `loc_id` | fixed | `location_merged.xlsx` |
| Location YoY | `loc_id` + `year` | fixed | `location_merged.xlsx` |
| Location QoQ | `loc_id` + `quarter` | fixed | `location_merged.xlsx` |
| City (Overall) | `city_id` | fixed | `city_merged.xlsx` |
| City YoY | `city_id` + `year` | fixed | `city_merged.xlsx` |
| City QoQ | `city_id` + `quarter` | fixed | `city_merged.xlsx` |

**Bin strategies:**
- `mean` — bins centred around the data mean (project level, adapts per project)
- `fixed` — bins from per-city range overrides in `config.py` (location/city level, consistent across all rows)

---

## Configuration

### Per-city range overrides

City-specific ranges override global defaults for area, rate, and price bins. Only keys that differ need to be listed in `CITY_RANGES`:

| City | MIN_RATE | MAX_RATE | MIN_AREA | MAX_AREA | MAX_PRICE |
|------|----------|----------|----------|----------|-----------|
| Mumbai | 2,000 | 40,000 | 200 | 5,000 | 5 Cr |
| Pune | 2,000 | 40,000 | 200 | 4,000 | 1.5 Cr |
| Thane | 2,000 | 40,000 | 200 | 4,500 | 2 Cr |
| Dubai | 1,000 | 4,000 | 300 | 1,000 | 1 Cr |

Access at runtime via `get_city_ranges(city)` which merges overrides onto global defaults.

### Loading factors

Loading factors convert net carpet area → saleable area. Dubai uses 1.00 (actual area, no loading):

| City | Residential | Commercial |
|------|-------------|------------|
| Mumbai | 1.45 | 1.50 |
| Pune | 1.35 | 1.40 |
| Thane | 1.40 | 1.45 |
| Dubai | 1.00 | 1.00 |

### Key constants

| Constant | Value | Description |
|----------|-------|-------------|
| `PRICE_STEP` | ₹20,00,000 | Bin width for price ranges |
| `AREA_STEP` | 200 sqft | Bin width for area ranges |
| `RATE_STEP` | ₹1,000/sqft | Bin width for rate ranges |
| `AGE_INTERVAL` | 5 years | Age bucket width (25–55, with `<25` and `>55` catch-alls) |

---

## Stats Engines

### `stats/rate.py`
Computes 90th-percentile rates, prevailing rate bands (percentile ± band%), floor-wise rate buckets, and full rate-range distributions (`unit_sold`, `total_sales`, `carpet_area_consumed` per bucket).

### `stats/area.py`
Assigns carpet sqft to area-range buckets. Supports mean-centred bins (project level) and fixed min/max bins (location/city level). Returns `count`, `sum`, or `mean` aggregations.

### `stats/price.py`
Formats INR values as `Cr / L / K` labels. Builds price-range buckets using mean ± 2 steps (project) or fixed bounds (location/city). Returns `unit_sold`, `total_sales`, `carpet_area_consumed` per bucket.

### `stats/age.py`
The `age` column is a list of buyer ages per transaction (extracted from Marathi-format `purchaser_name` strings). The engine explodes these lists so each age gets its own row, then bins into 5-year buckets from `<25` to `>55`.

### `stats/buyer.py`
Groups transactions by `buyer_pincode` and returns per-pincode count, percentage share, total and average agreement price. Also produces a top-10 buyer pincodes dict per project/location/city.

---

## Output Column Naming

All output columns follow the pattern `{segment}_{metric}` where `segment` is a property type (e.g. `Flat`, `Shop`) or BHK/br label (e.g. `2 BHK`, `3 br`).

> [!TIP]
> **Naming Standardization**: The pipeline automatically ensures a single space between the number and the suffix ("1 BHK", "2 br") while preserving the original "BHK" or "br" suffix as provided in your mapping and RERA keywords.

| Metric Suffix | Description |
|---------------|-------------|
| `_sold_igr` | Unit count |
| `_total_agreement_price` | Total sales value |
| `_avg_agreement_price` | Average sales value |
| `_ca_consumed_sqft_igr` | Carpet area consumed |
| `_wt_avg_rate_nca` / `_p50` / `_p75` / `_p90` | Rate on net carpet area (percentiles) |
| `_wt_avg_rate_sa` / `_p50` / `_p75` / `_p90` | Rate on saleable area (percentiles) |
| `_floor_wise_90p_rate` | Dict of floor bucket → 90P rate |
| `_most_prevailing_rate_range` | Lower–upper rate range string |
| `_agreement_price_range_unit_sold` / `_total_sales` / `_ca_consumed_sqft` | Price bucket dicts |
| `_rate_range_unit_sold` / `_total_sales` / `_ca_consumed_sqft` | Rate bucket dicts |
| `_age_range_unit_sold` / `_total_agreement_price` / `_ca_consumed_sqft` | Age bucket dicts |

---

## Known Issues

### 🟡 `preprocessing.py` — Wrong condition for loading factor (line 102)

`"Others"` is a catch-all fallback label, not a residential type. Any property that couldn't be classified silently gets residential loading applied, inflating its `saleable_sqft` and `rate_on_sa`.

```python
# ❌ Current (wrong)
df["property_type"].isin(["Flat", "Others"])

# ✅ Fix — RESIDENTIAL_TYPES is already imported at the top of the file
df["property_type"].isin(RESIDENTIAL_TYPES)
```

---

## Adding a New City

1. Add the city to the `city` table in PostgreSQL with a unique `city_id`
2. Add a `CITY_RANGES` entry in `config.py` with the appropriate min/max bounds
3. Add a `CITY_LOADING` entry in `config.py` if loading factors differ from global defaults
4. Ensure transaction rows in `property_transaction_db1` carry the correct `city_id`
5. Run `python run.py` and select the new city