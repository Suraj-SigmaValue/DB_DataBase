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
    - BHK columns are reordered numerically (<1 BHK, 1 BHK, 1.5 BHK ... >3 BHK, 4 BHK)
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
RERA_KEYWORDS_PATH = r"D:\DataBase\DB_DataBase\Required_Excels\RERA_All_Keywords_BHK_Prop_Type.xlsx"
PROP_TYPE_PATH     = r"D:\DataBase\DB_DataBase\Required_Excels\Property_type_keywords.xlsx"
OUTPUT_DIR         = r"D:\DataBase\DB_DataBase\Output"

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
        df = pd.read_sql(query, conn, params={"city_id": city_id}).head(5000)  # Limit to first 1000 rows

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
    """Write ``df`` into the given PostgreSQL ``table`` with better error handling."""
    
    engine = get_engine()
    
    # Prepare DataFrame for database insertion
    print(f"    Preparing {len(df):,} rows for database insertion...")
    prep_start = time.time()
    df_prepared = prepare_df_for_db(df)
    print(f"    Preparation completed in {time.time()-prep_start:.1f}s")
    
    # Save to database with chunking for better performance
    print(f"    Inserting into table '{table}'...")
    insert_start = time.time()
    
    try:
        # Use chunksize for large DataFrames to avoid memory issues
        chunksize = 1000
        df_prepared.to_sql(
            table, 
            engine, 
            index=False, 
            if_exists=if_exists,
            chunksize=chunksize,
            method='multi'  # Use multi-row insert for better performance
        )
        print(f"    ✓ Inserted {len(df):,} rows into '{table}' in {time.time()-insert_start:.1f}s")
        
    except Exception as e:
        print(f"    ✗ Database error: {e}")
        print(f"    Trying with different approach...")
        
        # Fallback: Try inserting in smaller chunks with error handling
        try:
            # Create table if it doesn't exist (with just the first row to define schema)
            if if_exists == 'replace':
                df_prepared.head(1).to_sql(table, engine, index=False, if_exists='replace')
            
            # Insert in small chunks
            chunk_size = 100
            for i in range(0, len(df_prepared), chunk_size):
                chunk = df_prepared.iloc[i:i+chunk_size]
                chunk.to_sql(table, engine, index=False, if_exists='append')
                print(f"      Inserted rows {i} to {min(i+chunk_size, len(df_prepared))}", end='\r')
            print(f"\n    ✓ Successfully inserted all rows using fallback method")
            
        except Exception as e2:
            print(f"    ✗ Fallback also failed: {e2}")
            # Save problematic DataFrame for debugging
            error_file = f"db_error_{table}_{time.strftime('%Y%m%d_%H%M%S')}.pkl"
            df_prepared.to_pickle(error_file)
            print(f"    Saved problematic data to {error_file} for debugging")

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
    """Save a result to disk (Excel/CSV) AND optionally to the database."""
    
    # === ALWAYS SAVE TO EXCEL/CSV (this is your existing functionality) ===
    print(f"\n  --- Saving to Excel file: {os.path.basename(out_path)} ---")
    
    # NEW: Delete existing file if it exists
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
            print(f"    Existing file deleted: {os.path.basename(out_path)}")
        except PermissionError:
            print(f"    ⚠ Could not delete existing file (likely open in Excel). Proceeding with robust save...")
        except Exception as e:
            print(f"    ⚠ Error deleting file: {e}")

    excel_start = time.time()
    save_success = False
    try:
        if df.shape[1] > 16384:
            print(f"  ⚠ Too many columns ({df.shape[1]}) — saving as CSV instead")
            out_path = out_path.replace(".xlsx", ".csv")
            df.to_csv(out_path, index=False)
        else:
            df.to_excel(out_path, index=False)
        save_success = True
    except PermissionError:
        print(f"  ✗ Permission Error: Could not write to {out_path}.")
        print(f"    Please ensure the file is closed in Excel and try again.")
        
        # Fallback: Save with timestamp to avoid losing work
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        base, ext = os.path.splitext(out_path)
        alt_path = f"{base}_{timestamp}{ext}"
        print(f"    → Falling back to: {os.path.basename(alt_path)}")
        
        try:
            if df.shape[1] > 16384:
                df.to_csv(alt_path, index=False)
            else:
                df.to_excel(alt_path, index=False)
            save_success = True
            out_path = alt_path # Update path for logging
        except Exception as e:
            print(f"    ✗ Fallback also failed: {e}")

    if save_success:
        print(f"  ✓ File saved: {os.path.basename(out_path)}  ({len(df):,} rows × {df.shape[1]} cols) in {time.time()-excel_start:.1f}s")
    else:
        print(f"  ✗ FAILED to save file: {os.path.basename(out_path)}")
    
    # === ADDITIONALLY SAVE TO DATABASE IF CONFIGURED ===
    if table and SAVE_TO_DB:
        print(f"  --- Saving to database table: {table} ---")
        db_start = time.time()
        save_to_db(df, table)
        print(f"  ✓ Database save completed in {time.time()-db_start:.1f}s")


# ─────────────────────────────────────────────────────────────────────────────
# BHK COLUMN REORDER
# ─────────────────────────────────────────────────────────────────────────────
# Keeps all non-BHK columns in their original order.
# Collects all BHK columns, groups by prefix sorted numerically,
# metrics in consistent order within each group, appended at the end.
#
# Before: ..., 2 BHK_sold, 1 BHK_sold, 3 BHK_sold, 2 BHK_avg_price, 1 BHK_avg_price ...
# After:  ...(non-BHK original order)...,
#         <1 BHK_sold, <1 BHK_avg_price, ...,
#          1 BHK_sold,  1 BHK_avg_price, ...,
#         1.5 BHK_sold, ...,
#          2 BHK_sold,  2 BHK_avg_price, ...,
#         >3 BHK_sold, ...,
#          4 BHK_sold,  ...

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
    """True if column belongs to a bhk/br prefix e.g. '1 BHK_', '2.5 br_', '<1 BHK_', '>3 br_'"""
    # Standardized labels from mapping are like '1 BHK', '1 br'
    return bool(re.match(r'^[<>]?\d+(\.\d+)?\s*(BHK|br)_', col, re.IGNORECASE))


def _get_br_prefix(col: str):
    """'2 BHK_sold_igr' → '2 BHK',  '<1 br_avg_agreement_price' → '<1 br'"""
    m = re.match(r'^([<>]?\d+(?:\.\d+)?\s*(?:BHK|br))_', col, re.IGNORECASE)
    return m.group(1) if m else None


def _br_prefix_num(prefix: str) -> float:
    """Numeric sort key: <1 BHK=0.5, 1 br=1.0, 1.5 BHK=1.5, >3 br=3.5, 4 BHK=4.0"""
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
    Keep non-BHK columns in original order.
    Sort BHK columns numerically by prefix, metrics in consistent order within each group.
    """
    all_cols    = df.columns.tolist()
    non_br_cols = [c for c in all_cols if not _is_br_col(c)]
    br_cols     = [c for c in all_cols if _is_br_col(c)]

    if not br_cols:
        return df   # nothing to reorder

    # Collect unique BHK prefixes then sort numerically
    seen, br_prefixes = set(), []
    for c in br_cols:
        p = _get_br_prefix(c)
        if p and p not in seen:
            br_prefixes.append(p)
            seen.add(p)

    sorted_prefixes = sorted(br_prefixes, key=_br_prefix_num)

    # Build ordered BHK section: each prefix → metrics in consistent order
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
                if period_type == "YoY" and "year" in result.columns:
                    result.insert(0, "Period", result["year"])
                    result.drop(columns=["year"], inplace=True)
                elif period_type == "QoQ" and "quarter" in result.columns:
                    result.insert(0, "Period", result["quarter"])
                    result.drop(columns=["quarter"], inplace=True)
                else:
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
        "project":  "ADB1_Project_Wise.xlsx",
        "location": "ADB1_Location_Wise.xlsx",
        "city":     "ADB1_City_Wise.xlsx",
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

        # Always save to Excel, and also to DB if SAVE_TO_DB=true
        table = DB_OUTPUT_TABLES.get(category) if SAVE_TO_DB else None
        save_result(final, out_path, table=table)

    print(f"\nAll done in {time.time()-total_start:.1f}s")


if __name__ == "__main__":
    main()