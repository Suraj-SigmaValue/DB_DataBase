"""
preprocessing.py
================
Raw DataFrame -> clean, analysis-ready DataFrame.
"""

import re

import numpy as np
import pandas as pd
from rapidfuzz import process as fuzz_process, fuzz

from config import (
    FLOOR_MAP,
    RESIDENTIAL_LOADING,
    COMMERCIAL_LOADING,
    RESIDENTIAL_TYPES,
    COMMERCIAL_TYPES,
)
from config import get_city_loading

def classify_project_type(property_type_raw: str) -> str:
    if property_type_raw in RESIDENTIAL_TYPES:
        return "Residential"
    if property_type_raw in COMMERCIAL_TYPES:
        return "Commercial"
    return "Other"


def extract_age(text: str) -> list:
    """Extract buyer ages from Marathi-format purchaser_name strings."""
    try:
        entries = re.split(r"\s*\d+\)\s*", text)[1:]
        numbers = []
        for entry in entries:
            _AGE_PATTERN = re.compile(
                    r"(?:वय[:-]?\s*(\d{2})|(\d{2}))\s*(?:;|\s)*पत्ता|(\d{2})\s+प्लॉट|(\d{2})\s*-,"
                )
            match = _AGE_PATTERN.search(entry)
            if match:
                num = next((int(n) for n in match.groups() if n), None)
                if num:
                    numbers.append(num)
        return numbers
    except Exception:
        return []


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    
    df.columns = df.columns.str.lower()

    # make sure critical columns exist before the downstream aggregators
    if "property_type" not in df.columns:
        print("  ⚠ 'property_type' column not found — filling with 'Other'")
        if "property_type_raw" in df.columns:
            df["property_type"] = (
                df["property_type_raw"]
                .astype(str)
                .str.strip()
                .str.title()
                .replace({"": "Other", "None": "Other"})
            )
        else:
            df["property_type"] = "Other"
    if "location_id" not in df.columns:
        if "loc_id" in df.columns:
            df["location_id"] = df["loc_id"]
        else:
            print("  ⚠ 'location_id' column not found — filling with NaN")
            df["location_id"] = np.nan

    # Floor number normalisation — skip if column doesn't exist
    if "floor_no" in df.columns:
        df["floor_no"] = df["floor_no"].replace(FLOOR_MAP).astype(float)
    else:
        print("  ⚠ 'floor_no' column not found — filling with NaN")
        df["floor_no"] = np.nan

    # Age extraction — skip if column doesn't exist
    if "purchaser_name" in df.columns:
        df["age"] = df["purchaser_name"].apply(extract_age)
    else:
        print("  ⚠ 'purchaser_name' column not found — filling age with empty list")
        df["age"] = [[] for _ in range(len(df))]

    # Derived area & rate columns — skip if columns don't exist
    if "net_carpet_area_sqmt" in df.columns:
        df["carpet_sqft"] = df["net_carpet_area_sqmt"] * 10.764
    else:
        print("  ⚠ 'net_carpet_area_sqmt' column not found — filling with NaN")
        df["carpet_sqft"] = np.nan

    mask = df['property_category'] == 'Sale'

    if "agreement_price" in df.columns:
        # ensure the entire column is numeric first to avoid dtype errors
        # when replacing only a subset of the rows (see earlier traceback).
        df["agreement_price"] = pd.to_numeric(df["agreement_price"], errors="coerce")
        df.loc[mask, "agreement_price"] = pd.to_numeric(
            df.loc[mask, "agreement_price"], errors="coerce"
        )
    else:
        print("  ⚠ 'agreement_price' column not found — filling with NaN")
        df["agreement_price"] = np.nan

    df["carpet_sqft"] = df["carpet_sqft"].replace(0, float('nan'))

    if "agreement_price" in df.columns and "carpet_sqft" in df.columns:
        df.loc[mask, "rate_on_net_ca"] = (
            df.loc[mask, "agreement_price"] / df.loc[mask, "carpet_sqft"]
        )
    else:
        df["rate_on_net_ca"] = np.nan

    # Saleable area
    if "net_carpet_area_sqmt" in df.columns and "property_type" in df.columns:
        # Default to global loading factors
        res_loading = df["city"].map(
            lambda c: get_city_loading(c)["RESIDENTIAL_LOADING"]
        )
        com_loading = df["city"].map(
            lambda c: get_city_loading(c)["COMMERCIAL_LOADING"]
        )

        df["saleable_sqft"] = np.where(
            df["property_type"].isin(["Flat", "Others"]),
            df["net_carpet_area_sqmt"] * res_loading,
            df["net_carpet_area_sqmt"] * com_loading,
        )

        df["saleable_sqft"] = df["saleable_sqft"].replace(0, float("nan"))
        df.loc[mask, "rate_on_sa"] = (
            df.loc[mask, "agreement_price"] / df.loc[mask, "saleable_sqft"]
        )
        
    else:
        df["saleable_sqft"] = np.nan
        df["rate_on_sa"]    = np.nan

    if "property_type_raw" in df.columns:
        # Fill missing/blank project_type using property_type_raw
        mask = df["project_type"].isna() | (df["project_type"].astype(str).str.strip() == "")
        df.loc[mask, "project_type"] = df.loc[mask, "property_type_raw"].apply(classify_project_type)
    else:
        print("  ⚠ 'property_type_raw' column not found — filling with 'Other'")
        df["project_type"] = df.get("project_type", "Other")

    # Buyer pincode
    if "buyer_pincode" in df.columns:
        df["buyer_pincode"] = pd.to_numeric(df["buyer_pincode"], errors="coerce")
    else:
        print("  ⚠ 'buyer_pincode' column not found — filling with NaN")
        df["buyer_pincode"] = np.nan

    return df



def load_bhk_mapping(rera_keywords_path: str) -> dict:
    
    """
    Load BHK keyword mapping from RERA excel.
    Returns {raw_bhk: final_bhk} dict.
    """
    rera = pd.read_excel(rera_keywords_path)
    rera["BHK"]       = rera["BHK"].str.strip().str.title()
    rera["standard_label"] = rera["standard_label"].str.strip().str.title()
    return (
        rera[["BHK", "standard_label"]]
        .drop_duplicates()
        .set_index("BHK")["standard_label"]
        .to_dict()
    )

def apply_bhk_mapping(df: pd.DataFrame, bhk_mapping: dict) -> pd.DataFrame:
    """Map raw BHK values to standardised standard_label values."""
    df = df.copy()
    df["bhk_br"] = df["bhk_br"].str.strip().str.title().map(bhk_mapping)
    return df

def load_prop_mapping(prop_type_path: str) -> dict:
    
    """
    Load Property_type keyword mapping from DB1 excel.
    Returns {raw_bhk: final_property_type} dict.
    """
    df = pd.read_excel(prop_type_path)
    df["property_type_raw"]       = df["property_type_raw"].str.strip().str.title()
    df["property_type_refined"] = df["property_type_refined"].str.strip().str.title()
    return (
        df[["property_type_raw", "property_type_refined"]]
        .drop_duplicates()
        .set_index("property_type_raw")["property_type_refined"]
        .to_dict()
    )



def apply_prop_mapping(df: pd.DataFrame, prop_mapping: dict) -> pd.DataFrame:
    """
    Map raw property types to refined labels.
    Raises error if any unmapped values are found.
    """
    df = df.copy()

    # Normalize column, treating missing values as 'Others' early so they
    # don't show up as numpy.nan in the unmapped set below.
    df["property_type_raw"] = (
        df["property_type_raw"].fillna("Others")
        .astype(str)
        .str.strip()
        .str.title()
        .replace({"Nan": "Others", "": "Others", "None": "Others"})
    )

    # # fuzzy normalize before mapping check
    # known_values = list(prop_mapping.keys())
    # df = normalize_property_type_raw(df, known_values, threshold=80)

    # Find unmapped values (dropna is now only a safety belt).
    unmapped = set(df["property_type_raw"].dropna().unique()) - set(prop_mapping.keys())

    if unmapped:
        raise ValueError(
            f"\n❌ Unmapped property types found:\n"
            f"{sorted(unmapped)}\n\n"
            f"Please add them to your property type mapping file."
        )

    # Apply mapping
    df["property_type_raw"] = df["property_type_raw"].map(prop_mapping)


    return df

def round_dict_floats(val, decimals=2):
    """Recursively round all float values inside a dict."""
    if isinstance(val, dict):
        return {k: round_dict_floats(v, decimals) for k, v in val.items()}
    if isinstance(val, float):
        return round(val, decimals)
    return val




def normalize_property_type_raw(
    df: pd.DataFrame,
    known_values: list,
    threshold: int = 80,        # match confidence — lower = more aggressive matching
) -> pd.DataFrame:
    """
    For any property_type_raw value not in known_values,
    attempt fuzzy match to the closest known value.
    If confidence is below threshold, fall back to 'Others'.

    Logs every substitution made so you can audit them.
    """
    df = df.copy()
    unique_vals   = df["property_type_raw"].unique()
    known_set     = set(known_values)
    substitutions = {}   # track what got changed

    for val in unique_vals:
        if val in known_set:
            continue   # already clean, skip

        # Try fuzzy match
        match, score, _ = fuzz_process.extractOne(
            val,
            known_values,
            scorer=fuzz.token_sort_ratio,   # handles word-order differences too
        )

        if score >= threshold:
            substitutions[val] = match
        else:
            substitutions[val] = "Others"   # low confidence → Others

    # Apply all substitutions at once
    if substitutions:
        print("\n  📋 Property type normalizations applied:")
        for original, replacement in substitutions.items():
            count = (df["property_type_raw"] == original).sum()
            print(f"     '{original}' → '{replacement}'  ({count} rows)")
        df["property_type_raw"] = df["property_type_raw"].replace(substitutions)

    return df