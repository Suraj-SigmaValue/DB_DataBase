"""
run.py
======
Entry point. Load → preprocess → BHK map → run pipeline → export.

Folder structure expected:
    Mumbai/
        Mumbai_Bandra_igr_processed_data_db1.xlsx
        Mumbai_Andheri_igr_processed_data_db1.xlsx
        Mumbai_Dadar_igr_processed_data_db1.xlsx
    Pune/
        Pune_Akurdi_igr_processed_data_db1.xlsx
        Pune_Kothrud_igr_processed_data_db1.xlsx
    ...

Rules:
    - Each folder contains multiple Excel files
    - Every file must be prefixed with the city name  e.g. Mumbai_*.xlsx
    - Each city is processed separately through pipelines (avoids column explosion)
    - Results are concatenated as rows into one output file per pipeline
    - BR columns are reordered numerically (<1br, 1br, 1.5br ... >3br, 4br)
    - All other columns stay in original order
"""

import sys
import os
import re
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from sqlalchemy import create_engine, text

from preprocessing import preprocess, load_bhk_mapping, apply_bhk_mapping, load_prop_mapping, apply_prop_mapping
from aggregators.project  import build_project_wise, build_yoy_project_wise, build_qoq_project_wise
from aggregators.location import build_location_wise, build_yoy_location_wise, build_qoq_location_wise
from aggregators.city     import build_city_wise, build_yoy_city_wise, build_qoq_city_wise
from config import (
    get_city_ranges,
    DB_CONFIG,
    DB_CITIES_TABLE,
    DB_TRANSACTIONS_TABLE,
    SAVE_TO_DB,
    DB_OUTPUT_TABLES,
)

# ── Output directory ──────────────────────────────────────────────────────────
RERA_KEYWORDS_PATH = r"D:\Adb1_code\ADB-Creation-Code\Required_Excels\RERA_All_Keywords_BHK_Prop_Type.xlsx"
PROP_TYPE_PATH     = r"D:\Adb1_code\ADB-Creation-Code\Required_Excels\Property_type_keywords.xlsx"
OUTPUT_DIR         = r"D:\Adb1_code\ADB-Creation-Code\Required_Excels\Output"

# Columns that must exist in every city's data
EXPECTED_COLUMNS = [
    "floor_no", "purchaser_name", "net_carpet_area_sqmt",
    "agreement_price", "property_category", "property_type",
    "property_type_raw", "project_type", "buyer_pincode",
    "transaction_date", "document_no",
]


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def get_engine():
    """Create and return a SQLAlchemy engine from DB_CONFIG."""
    cfg = DB_CONFIG
    url = (
        f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}"
        f"@{cfg['host']}:{cfg['port']}/{cfg['dbname']}"
    )
    return create_engine(url)


def fetch_available_cities(engine) -> dict:
    """
    Query the cities table and return {city_name: city_id}.
    """
    query = text(f"SELECT city_id, city_name FROM {DB_CITIES_TABLE} ORDER BY city_name")
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
    return {row.city_name: row.city_id for row in rows}


def load_city_from_db(engine, city: str, city_id: int) -> pd.DataFrame:
    """
    Load all transactions for a single city by joining
    transaction_db1 with the cities table on city_id.
    Tags each row with the city name.
    """
    query = text(f"""
        SELECT t.*
        FROM {DB_TRANSACTIONS_TABLE} t
        WHERE t.city_id = :city_id
    """)

    print(f"  Querying DB for {city} (city_id={city_id})...")
    t0 = time.time()

    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"city_id": city_id})

    df["city"] = city

    print(f"    ✓ {city}: {len(df):,} rows loaded ({time.time()-t0:.1f}s)")

    missing_cols = [c for c in EXPECTED_COLUMNS if c not in df.columns.str.lower().tolist()]
    if missing_cols:
        print(f"    ⚠ Missing columns in {city}: {missing_cols}")

    return df


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def select_cities(available_cities: list) -> list:
    """Interactively ask the user which cities to process."""
    print("\n" + "="*50)
    print("  CITY SELECTION")
    print("="*50)
    print("Available cities:")
    for i, city in enumerate(available_cities, 1):
        print(f"  {i}. {city}")
    print(f"  {len(available_cities)+1}. ALL cities")
    print("="*50)
    print("Enter city numbers separated by commas (e.g. 1,3) or press Enter for ALL:")

    while True:
        raw = input("Your choice: ").strip()

        if raw == "":
            print(f"  → Loading ALL cities: {available_cities}")
            return available_cities

        try:
            choices = [int(x.strip()) for x in raw.split(",")]
        except ValueError:
            print("  ✗ Invalid input. Enter numbers only, e.g. 1,3")
            continue

        all_option = len(available_cities) + 1

        if any(c < 1 or c > all_option for c in choices):
            print(f"  ✗ Numbers must be between 1 and {all_option}")
            continue

        if all_option in choices:
            print(f"  → Loading ALL cities: {available_cities}")
            return available_cities

        selected = [available_cities[c - 1] for c in choices]
        print(f"  → Loading: {selected}")
        return selected



# -----------------------------------------------------------------------------
# database helpers
# -----------------------------------------------------------------------------



def get_db_engine():
    """Return a SQLAlchemy engine built from ``config.DB_CONFIG``.

    The config dictionary already exposes host/port/dbname/user/password and
    is populated from the environment via ``dotenv`` in :mod:`config`.  We
    construct the usual ``postgresql+psycopg2`` URL that pandas understands
    via ``DataFrame.to_sql``.
    """
    cfg = DB_CONFIG
    url = (
        f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}@"
        f"{cfg['host']}:{cfg['port']}/{cfg['dbname']}"
    )
    return create_engine(url)


def save_to_db(df: pd.DataFrame, table: str, if_exists: str = "replace"):
    """Write ``df`` into the given PostgreSQL ``table``."""
    import json  # Add at top of file if not already there
    
    engine = get_engine()
    
    # Prepare DataFrame for database insertion
    print(f"  Preparing {len(df)} rows for database insertion...")
    df_prepared = prepare_df_for_db(df)
    
    # Save to database
    df_prepared.to_sql(table, engine, index=False, if_exists=if_exists)
    print(f"  Saved to table {table}  ({len(df):,} rows × {df.shape[1]} cols)")

def prepare_df_for_db(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert any dictionary/object columns to JSON strings for PostgreSQL compatibility.
    Also handles any other non-serializable types.
    """
    df_copy = df.copy()
    
    for col in df_copy.columns:
        # Check if column contains dicts or lists
        sample = df_copy[col].dropna()
        if len(sample) > 0:
            # Check if the first non-null value is a dict or list
            first_val = sample.iloc[0]
            if isinstance(first_val, (dict, list)):
                # print(f"  Converting column '{col}' to JSON string")
                df_copy[col] = df_copy[col].apply(
                    lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x
                )
            # Also check for mixed types that might contain dicts
            elif df_copy[col].dtype == 'object':
                # Check if any value in the column is a dict/list
                has_dict = any(isinstance(x, (dict, list)) for x in sample.head(100))
                if has_dict:
                    # print(f"  Converting column '{col}' to JSON string (contains dicts/lists)")
                    df_copy[col] = df_copy[col].apply(
                        lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x
                    )
    
    return df_copy


def save_result(df: pd.DataFrame, out_path: str, table: str | None = None):
    """Save a result either to disk (Excel/CSV) or to the database.

    Pass ``table`` when you want the frame written to PostgreSQL.  If both
    ``table`` and ``out_path`` are provided the database write takes
    precedence; the file path is only used for diagnostic messages.
    """
    if table:
        save_to_db(df, table)

    # existing file-based behaviour
    if df.shape[1] > 16384:
        print(f"  ⚠ Too many columns ({df.shape[1]}) — saving as CSV instead")
        out_path = out_path.replace(".xlsx", ".csv")
        df.to_csv(out_path, index=False)
    else:
        df.to_excel(out_path, index=False)
    print(f"  Saved: {os.path.basename(out_path)}  ({len(df):,} rows × {df.shape[1]} cols)")


# ─────────────────────────────────────────────────────────────────────────────
# BR COLUMN REORDER
# ─────────────────────────────────────────────────────────────────────────────
# Keeps all non-br columns in their original order.
# Collects all br columns, groups by prefix sorted numerically,
# metrics in consistent order within each group, appended at the end.
#
# Before: ..., 2br_sold, 1br_sold, 3br_sold, 2br_avg_price, 1br_avg_price ...
# After:  ...(non-br original order)...,
#         <1br_sold, <1br_avg_price, ...,
#          1br_sold,  1br_avg_price, ...,
#         1.5br_sold, ...,
#          2br_sold,  2br_avg_price, ...,
#         >3br_sold, ...,
#          4br_sold,  ...

_BR_METRIC_ORDER = [
    "_sold_igr",
    "_total_agreement_price",
    "_avg_agreement_price",
    "_ca_consumed_sqft_igr",
    "_wt_avg_rate_nca",
    "_p50_rate_nca",
    "_p75_rate_nca",
    "_p90_rate_nca",
    "_wt_avg_rate_sa",
    "_p50_rate_sa",
    "_p75_rate_sa",
    "_p90_rate_sa",
    "_floor_wise_90p_rate",
    "_most_prevailing_rate_range",
    "_total_unit_sold_in_rate_range",
    "_total_unit_sold_in_area_range",
    "_total_agreement_price_in_area_range",
    "_avg_agreement_price_in_area_range",
    "_total_ca_consumed_in_area_range_sqft",
    "_avg_carpet_area_in_sqft",
    "_agreement_price_range_unit_sold",
    "_agreement_price_range_total_sales",
    "_agreement_price_range_ca_consumed_sqft",
    "_rate_range_unit_sold",
    "_rate_range_total_sales",
    "_rate_range_ca_consumed_sqft",
    "_age_range_unit_sold",
    "_age_range_total_agreement_price",
    "_age_range_ca_consumed_sqft",
]

# Sorted longest-first so most specific suffix is matched first
_BR_SUFFIXES_SORTED = sorted(_BR_METRIC_ORDER, key=len, reverse=True)


def _is_br_col(col: str) -> bool:
    """True if column belongs to a bhk prefix e.g. '1 Bhk_', '2.5 Bhk_', '<1 Bhk_', '>3 Bhk_'"""
    # Standardized labels from mapping are like '1 Bhk', '2 Bhk'
    return bool(re.match(r'^[<>]?\d+(\.\d+)?\s*Bhk_', col, re.IGNORECASE))


def _get_br_prefix(col: str):
    """'2 Bhk_sold_igr' → '2 Bhk',  '<1 Bhk_avg_agreement_price' → '<1 Bhk'"""
    m = re.match(r'^([<>]?\d+(?:\.\d+)?\s*Bhk)_', col, re.IGNORECASE)
    return m.group(1) if m else None


def _br_prefix_num(prefix: str) -> float:
    """Numeric sort key: <1 Bhk=0.5, 1 Bhk=1.0, 1.5 Bhk=1.5, >3 Bhk=3.5, 4 Bhk=4.0"""
    digits = re.sub(r"[^0-9.]", "", prefix) or "0"
    n = float(digits)
    if prefix.startswith("<"):
        return n - 0.5
    if prefix.startswith(">"):
        return n + 0.5
    return n


def _br_metric_key(col: str) -> int:
    """Sort key for metric within a br prefix."""
    for sfx in _BR_SUFFIXES_SORTED:
        if col.endswith(sfx):
            try:
                return _BR_METRIC_ORDER.index(sfx)
            except ValueError:
                return 999
    return 999


def reorder_br_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep non-br columns in original order.
    Sort br columns numerically by prefix, metrics in consistent order within each group.
    """
    all_cols    = df.columns.tolist()
    non_br_cols = [c for c in all_cols if not _is_br_col(c)]
    br_cols     = [c for c in all_cols if _is_br_col(c)]

    if not br_cols:
        return df   # nothing to reorder

    # Collect unique br prefixes then sort numerically
    seen, br_prefixes = set(), []
    for c in br_cols:
        p = _get_br_prefix(c)
        if p and p not in seen:
            br_prefixes.append(p)
            seen.add(p)

    sorted_prefixes = sorted(br_prefixes, key=_br_prefix_num)

    # Build ordered br section: each prefix → metrics in consistent order
    ordered_br = []
    for prefix in sorted_prefixes:
        prefix_cols = sorted(
            [c for c in br_cols if _get_br_prefix(c) == prefix],
            key=_br_metric_key,
        )
        ordered_br.extend(prefix_cols)

    final = non_br_cols + ordered_br

    # Safety — no column lost or duplicated
    if set(final) != set(all_cols):
        lost = set(all_cols) - set(final)
        print(f"  ⚠ reorder_br_columns: {len(lost)} unmatched cols appended at end")
        final += list(lost)

    return df[final]


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    total_start = time.time()

    # ── 1. Connect to DB and fetch available cities ───────────────────────────
    print("\nConnecting to PostgreSQL...")
    try:
        engine = get_engine()
        city_map = fetch_available_cities(engine)   # {city_name: city_id}
    except Exception as e:
        print(f"ERROR: Could not connect to database — {e}")
        sys.exit(1)

    if not city_map:
        print("ERROR: No cities found in the cities table.")
        sys.exit(1)

    available_cities = list(city_map.keys())
    print(f"  ✓ Found {len(available_cities)} cities: {available_cities}")

    # ── 2. Ask which cities to process ───────────────────────────────────────
    selected_cities = select_cities(available_cities)

    # ── 3. Load transaction data per selected city from DB ───────────────────
    print()
    city_dataframes = {}

    for city in selected_cities:
        city_id = city_map[city]
        try:
            df_city = load_city_from_db(engine, city, city_id)
            if df_city.empty:
                print(f"  ⚠ Skipping {city} — no data returned")
            else:
                city_dataframes[city] = df_city
        except Exception as e:
            print(f"  ✗ Failed to load {city}: {e}")

    if not city_dataframes:
        print("\nERROR: No data loaded at all. Check your folder paths.")
        sys.exit(1)

    # ── 4. Combine all cities for preprocessing + mapping ────────────────────
    # Preprocess and map once on the full dataset (efficient),
    # then split back by city for pipeline runs.

    df_raw = pd.concat(city_dataframes.values(), ignore_index=True)
    print(f"\n  Total rows loaded: {len(df_raw):,}")

    print("\nPreprocessing...")
    dataframe = preprocess(df_raw)

    print("\nApplying mappings...")
    try:
        bhk_mapping = load_bhk_mapping(RERA_KEYWORDS_PATH)
    except FileNotFoundError:
        print(f"ERROR: RERA keywords file not found -> {RERA_KEYWORDS_PATH}")
        sys.exit(1)

    try:
        prop_type_mapping = load_prop_mapping(PROP_TYPE_PATH)
    except FileNotFoundError:
        print(f"ERROR: Property type file not found -> {PROP_TYPE_PATH}")
        sys.exit(1)

    
    dataframe = apply_bhk_mapping(dataframe, bhk_mapping)
    dataframe = apply_prop_mapping(dataframe, prop_type_mapping)

    # ── 5. Define pipelines ───────────────────────────────────────────────────
    # Each entry: (label, build_fn, category, period_type, period_value)
    #   category    : "project" | "location" | "city"
    #   period_type : "Overall" | "YoY" | "QoQ"
    #   period_value: human-readable period label written into the Period column
    pipeline_defs = [
        ("Project",      build_project_wise,      "project",  "Overall", "Overall"),
        ("Project YoY",  build_yoy_project_wise,  "project",  "YoY",     "YoY"),
        ("Project QoQ",  build_qoq_project_wise,  "project",  "QoQ",     "QoQ"),
        ("Location",     build_location_wise,     "location", "Overall", "Overall"),
        ("Location YoY", build_yoy_location_wise, "location", "YoY",     "YoY"),
        ("Location QoQ", build_qoq_location_wise, "location", "QoQ",     "QoQ"),
        ("City",         build_city_wise,         "city",     "Overall", "Overall"),
        ("City YoY",     build_yoy_city_wise,     "city",     "YoY",     "YoY"),
        ("City QoQ",     build_qoq_city_wise,     "city",     "QoQ",     "QoQ"),
    ]

    # Accumulate in-memory: {category: [tagged DataFrames]}
    pipeline_results = {"project": [], "location": [], "city": []}

    # ── 6. Run each city separately through all pipelines ────────────────────
    #
    # WHY SEPARATELY:
    #   Running all cities in one build_fn() call causes pivot explosion.
    #   Each unique property_type and bhk_br value becomes a column.
    #   4 cities × 10 property types × 8 range buckets × 5 metrics = 30,000+ cols.
    #   Per-city: each city produces ~200 cols, then rows are stacked via concat.
    #   pd.concat aligns columns by name — missing ones filled with NaN (blank).
    #
    for city in selected_cities:

        if city not in city_dataframes:
            continue

        print(f"\n{'='*50}")
        print(f"  Processing: {city}")
        print(f"{'='*50}")

        city_df     = dataframe[dataframe["city"] == city].copy()
        city_ranges = get_city_ranges(city)

        print(f"  Rows: {len(city_df):,}")
        print(
            f"  Ranges → "
            f"Rate: {city_ranges['MIN_RATE']}-{city_ranges['MAX_RATE']} | "
            f"Area: {city_ranges['MIN_AREA']}-{city_ranges['MAX_AREA']} | "
            f"Price: {city_ranges['MIN_PRICE']}-{city_ranges['MAX_PRICE']}"
        )

        if city_df.empty:
            print(f"  ⚠ No rows after preprocessing — skipping {city}")
            continue

        for label, build_fn, category, period_type, period_value in pipeline_defs:
            print(f"\n  Running {label}...")
            t = time.time()
            try:

                if category in ("location", "city"):
                    result = build_fn(city_df, city_ranges=city_ranges)
                else:
                    result = build_fn(city_df)

                if result is None or result.empty:
                    print(f"  ⚠ Empty result — skipped")
                    continue

                # Tag with Type + Period before accumulating
                result.insert(0, "Period", period_value)
                result.insert(0, "Type",   period_type)

                pipeline_results[category].append(result)
                print(f"  → {len(result):,} rows × {result.shape[1]} cols ({time.time()-t:.1f}s)")

            except Exception as e:
                print(f"  ✗ FAILED [{label}]: {e}")

    # ── 7. Concat → reorder BR cols → save one merged file per category ───────
    print(f"\n{'='*50}")
    print("  Saving merged output files...")
    print(f"{'='*50}\n")

    output_filenames = {
        "project":  "project_merged.xlsx",
        "location": "location_merged.xlsx",
        "city":     "city_merged.xlsx",
    }

    for category, frames in pipeline_results.items():
        if not frames:
            print(f"  ✗ No data for {category} — skipped")
            continue

        # Stack all city × period results — gaps filled with NaN automatically
        final = pd.concat(frames, ignore_index=True)

        # Reorder only BR columns numerically — all other cols stay as-is
        final = reorder_br_columns(final)

        out_path = os.path.join(OUTPUT_DIR, output_filenames[category])

        if SAVE_TO_DB:
            table = DB_OUTPUT_TABLES.get(category)
            if not table:
                print(f"  ✗ No database table configured for {category}, skipping")
            else:
                save_result(final, out_path, table=table)
        else:
            save_result(final, out_path)

    print(f"\nAll done in {time.time()-total_start:.1f}s")


if __name__ == "__main__":
    main()