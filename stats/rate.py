"""
stats/rate.py
=============
Rate-related statistics:
  - percentile_rate()
  - most_prevailing_rate_on_ca()
  - create_rate_ranges()
  - get_floor_wise_90p_rate()
"""

import numpy as np
import pandas as pd

from config import RATE_STEP


def percentile_rate(
    df: pd.DataFrame,
    property_type: str,
    column_name: str = "rate_on_net_ca",
) -> float:
    """
    Custom 90th-percentile matching MMA chart methodology.
    Linear interpolation between the two bracketing values.
    """
    if "property_type" in df.columns:
        segment = df[(df["property_type"] == property_type) & (df[column_name] > 0)]
    else:
        segment = df[df[column_name] > 0]

    data = sorted(segment[column_name].tolist())
    n = len(data)
    if n == 0:
        return 0.0
    if n == 1:
        return data[0]
    index = (90 / 100) * (n - 1) + 1
    i = min(int(index), n - 1)
    v1 = data[i - 1]
    v2 = data[i]
    diff  = round(index - int(index), 1)
    return round(v1 + diff * (v2 - v1), 2)


def most_prevailing_rate_on_ca(
    df: pd.DataFrame,
    property_type: str,
    percentile: int = 90,
    band_pct: float = 0.05,
):
    """
    Generic prevailing rate band.
    percentile : percentile to use (e.g. 90, 80)
    band_pct   : band width (0.05 = ±5%)
    """
    if "property_type" in df.columns:
        segment = df[
            (df["property_type"] == property_type)
            & (df["rate_on_net_ca"] > 0)
        ]
    else:
        segment = df[df["rate_on_net_ca"] > 0]

    if segment.empty:
        return 0

    p_val = segment["rate_on_net_ca"].quantile(percentile / 100)

    lower = int(p_val * (1 - band_pct))
    upper = int(p_val * (1 + band_pct))

    return f"{lower}-{upper}"


def _build_rate_bins(mean: int, interval: int = RATE_STEP):
    """7 rate-range buckets centred on mean, compatible with pd.cut."""
    pts    = [mean + o * interval for o in range(-3, 4)]
    edges  = [-np.inf] + pts + [np.inf]
    labels = (
        [f"<{pts[0]}"]
        + [f"{pts[i]}-{pts[i+1]}" for i in range(len(pts) - 1)]
        + [f">{pts[-1]}"]
    )
    return edges, labels


def _build_rate_bins_generalize(
    min_val: float,
    max_val: float,
    interval: int = RATE_STEP,
):
    """
    Build bins strictly between user-provided min and max.
    """

    start = int(min_val)
    end   = int(max_val)

    inner = list(range(start, end + interval, interval))
    
    edges  = [-np.inf] + inner + [np.inf]
    labels = (
        [f"<{inner[0]}"]
        + [f"{inner[i]}-{inner[i+1]}" for i in range(len(inner) - 1)]
        + [f">{inner[-1]}"]
    )

    return edges, labels


def create_rate_ranges(
    df: pd.DataFrame,
    property_type: str,
    bin_strategy: str = "mean",
    interval: int = RATE_STEP,
    min_val: float = None,
    max_val: float = None,
) -> dict:
    if "property_type" in df.columns:
        segment = df[
            (df["property_type"] == property_type)
            & (df["rate_on_net_ca"] > 0)
        ].copy()
    else:
        segment = df[df["rate_on_net_ca"] > 0].copy()

    if segment.empty:
        return {}

    if bin_strategy == "mean":

        rate_mean = segment["rate_on_net_ca"].mean()
        mean = 0 if pd.isna(rate_mean) else int(round(rate_mean / interval) * interval)
        edges, labels = _build_rate_bins(mean, interval)

    elif bin_strategy == "fixed":

        if min_val is None or max_val is None:
            raise ValueError("min_val and max_val must be provided for fixed bin strategy")

        edges, labels = _build_rate_bins_generalize(min_val, max_val, interval)

    else:
        raise ValueError("Invalid bin_strategy")

    segment["rate_range"] = pd.cut(
        segment["rate_on_net_ca"],
        bins=edges,
        labels=labels,
        right=False,
        include_lowest=True,
    )

    result = segment["rate_range"].value_counts().to_dict()

    return {k: v for k, v in result.items() if pd.notna(v) and v != 0}



def get_floor_wise_90p_rate(df: pd.DataFrame, floor_interval: int = 5) -> dict:
    """
    90th-percentile rate_on_net_ca grouped into floor ranges.
    Returns {'0-5': 8500.0, '5-10': 9200.0, ...}
    Only buckets with data are returned. Rows with missing floor_no are ignored.
    """
    df = df[(df["rate_on_net_ca"] > 0) & (df["floor_no"].notna())].copy()
    if df.empty:
        return {}

    max_floor = int(df["floor_no"].max())
    upper     = ((max_floor // floor_interval) + 1) * floor_interval
    edges     = list(range(0, upper + floor_interval, floor_interval))
    labels    = [f"{edges[i]}-{edges[i+1]}" for i in range(len(edges) - 1)]

    df["floor_range"] = pd.cut(df["floor_no"], bins=edges, labels=labels, right=False)

    result = (
        df.groupby("floor_range", observed=False)["rate_on_net_ca"]
        .quantile(0.90)
        .round(2)
        .to_dict()
    )
    return {str(k): float(v) for k, v in result.items() if pd.notna(v)}


def floor_wise_wrapper(df: pd.DataFrame, segment_val) -> dict:
    """
    Thin wrapper for apply_and_merge compatibility.
    segment_val is already isolated by the groupby — just forwarded.
    """
    return get_floor_wise_90p_rate(df)




# ============================================================
# RATE RANGE STATS ENGINE  (sold / total price / carpet area)
# ============================================================

def _summarise_rate_ranges(df: pd.DataFrame) -> dict:
    """
    For a df that already has a 'rate_range' column,
    return unit_sold, total_sales, carpet_area_consumed per bucket.
    """
    summary = (
        df.groupby("rate_range", observed=False)
        .agg(
            unit_sold                  =("agreement_price",  "size"),
            total_sales                =("agreement_price",  "sum"),
            ca_consumed_sqft  =         ("carpet_sqft",      "sum"),
        )
        .reset_index()
    )
    idx = summary.set_index("rate_range")
    return {
        "unit_sold":                     idx["unit_sold"].to_dict(),
        "total_sales":                   idx["total_sales"].to_dict(),
        "ca_consumed_sqft":              idx["ca_consumed_sqft"].to_dict(),
    }


def create_rate_range_stats(
    df: pd.DataFrame,
    filter_col: str,
    filter_val,
    bin_strategy: str = "mean",
    interval: int = RATE_STEP,
    min_val: float = None,
    max_val: float = None,
) -> dict:
    """
    Generic rate-range statistics engine.
    Returns unit_sold / total_sales / carpet_area_consumed per rate bucket.

    bin_strategy : "mean" → bins centred on mean
                   "fixed" → user-supplied min_val / max_val
    """
    segment = df[df["rate_on_net_ca"] > 0].copy()
    if segment.empty:
        return {}

    # Apply filter
    if filter_col in segment.columns:
        segment = segment[segment[filter_col] == filter_val].copy()
    if segment.empty:
        return {}

    # Build bins
    if bin_strategy == "mean":
        rate_mean = segment["rate_on_net_ca"].mean()
        mean = 0 if pd.isna(rate_mean) else int(round(rate_mean / interval) * interval)
        edges, labels = _build_rate_bins(mean, interval)

    elif bin_strategy == "fixed":
        if min_val is None or max_val is None:
            raise ValueError("min_val and max_val required for fixed bin strategy")
        edges, labels = _build_rate_bins_generalize(min_val, max_val, interval)

    else:
        raise ValueError("Invalid bin_strategy. Use 'mean' or 'fixed'.")

    segment["rate_range"] = pd.cut(
        segment["rate_on_net_ca"],
        bins=edges,
        labels=labels,
        right=False,
        include_lowest=True,
    )

    result = _summarise_rate_ranges(segment)

    # Strip zero / NaN buckets from each sub-dict
    return {
        metric: {k: v for k, v in bucket.items() if pd.notna(v) and v != 0}
        for metric, bucket in result.items()
    }


# ── Property-type wrapper ──────────────────────────────────────────────────────

def create_rate_range_stats_by_property_type(
    df: pd.DataFrame,
    property_type: str,
    **kwargs,
) -> dict:
    """Unit sold / total sales / carpet area consumed — per rate range, per property type."""
    return create_rate_range_stats(df, "property_type", property_type, **kwargs)


# ── BHK wrapper ───────────────────────────────────────────────────────────────

def create_rate_range_stats_by_bhk(
    df: pd.DataFrame,
    bhk: str,
    **kwargs,
) -> dict:
    """Unit sold / total sales / carpet area consumed — per rate range, per BHK."""
    return create_rate_range_stats(df, "bhk_br", bhk, **kwargs)