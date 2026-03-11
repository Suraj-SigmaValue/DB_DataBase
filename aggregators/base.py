"""
aggregators/base.py
===================
Shared pipeline mechanics used by all aggregators
(project, location, city, etc.)
"""

import pandas as pd

from config import NON_BHK_VALUES


# ── Mask builder ──────────────────────────────────────────────────────────────

def build_masks(dataframe: pd.DataFrame, base_col: str = "proj_id") -> dict:
    """
    Build all boolean masks needed by the pipeline.

    Parameters
    ----------
    base_col : the column whose notna() defines the base population
               ('proj_id' for project-wise, 'city' for city-wise, etc.)
    """
    base_mask         = dataframe[base_col].notna()
    valid_price_mask  = dataframe["agreement_price"] > 0
    valid_area_mask   = dataframe["net_carpet_area_sqmt"] > 0
    valid_rate_mask   = valid_price_mask & valid_area_mask
    valid_carpet_mask = dataframe["carpet_sqft"] > 0
    non_bhk_mask      = ~dataframe["bhk_br"].isin(NON_BHK_VALUES)
    bhk_base_mask     = base_mask & non_bhk_mask

    return {
        "base":         base_mask,
        "valid_price":  valid_price_mask,
        "valid_area":   valid_area_mask,
        "valid_rate":   valid_rate_mask,
        "valid_carpet": valid_carpet_mask,
        "non_bhk":      non_bhk_mask,
        "bhk_base":     bhk_base_mask,
    }


# ── Pivot helpers ─────────────────────────────────────────────────────────────

def create_pivot(
    df: pd.DataFrame,
    group_cols: list,
    value_col: str,
    agg: str,
    suffix: str,
) -> pd.DataFrame:
    """
    Single pivot helper.
    agg : any pandas GroupBy aggregation string ('count', 'sum', 'mean')
    """
    return (
        df.groupby(group_cols)[value_col]
        .agg(agg)
        .unstack()
        .add_suffix(suffix)
        .reset_index()
    )


def apply_and_merge(
    df: pd.DataFrame, group_cols: list, func, suffix: str
) -> pd.DataFrame:
    """
    Apply a function to each group and unstack the last level (usually property_type or bhk_br).
    """
    import pandas as pd
    
    # In newer pandas (>= 2.2), apply() excludes group keys by default.
    # pandas 3.0+ disallows include_groups=True entirely.
    kwargs = {}
    try:
        # Check if we should explicitly pass include_groups=False (pandas >= 2.2 < 3.0)
        # to avoid DeprecationWarnings, or just rely on the default in 3.0.
        import pandas as pd
        from packaging import version
        # We don't have packaging, so we use a simpler check or just try/except
    except ImportError:
        pass

    try:
        # Try passing include_groups=False (the modern standard)
        res = df.groupby(group_cols).apply(
            lambda g: func(g, g.name[-1] if isinstance(g.name, tuple) else g.name),
            include_groups=False
        )
    except (TypeError, ValueError):
        # Fallback for older pandas or pandas 3.0+ if it behaves differently
        res = df.groupby(group_cols).apply(
            lambda g: func(g, g.name[-1] if isinstance(g.name, tuple) else g.name)
        )

    return (
        res
        .unstack()
        .add_suffix(suffix)
        .reset_index()
    )


def process_price_ranges(
    df: pd.DataFrame,
    group_cols: list,
    calc_func,
    proj_cols: list,
) -> pd.DataFrame:
    """
    Build a wide price-range DataFrame by calling calc_func per group.

    Parameters
    ----------
    proj_cols : the key columns (e.g. ['index', 'project_name'])
    """
    if df.empty:
        return pd.DataFrame(columns=proj_cols)

    rows = {}
    for (*key_parts, segment), group_data in df.groupby(group_cols):
        key = tuple(key_parts)
        rows.setdefault(key, {})[segment] = calc_func(group_data, segment)

    output = []
    for key, seg_data in rows.items():
        row = dict(zip(proj_cols, key))
        for segment, details in seg_data.items():
            for metric, value in details.items():
                row[f"{segment}_agreement_price_range_{metric}"] = value
        output.append(row)

    return pd.DataFrame(output)

def process_rate_ranges(
    df: pd.DataFrame,
    group_cols: list,
    calc_func,
    proj_cols: list,
) -> pd.DataFrame:
    """
    Build a wide rate-range DataFrame by calling calc_func per group.
    Mirrors process_price_ranges — same shape, different stat source.

    proj_cols : the key columns (e.g. ['index', 'project_name'])
    """
    if df.empty:
        return pd.DataFrame(columns=proj_cols)

    rows = {}
    for (*key_parts, segment), group_data in df.groupby(group_cols):
        key = tuple(key_parts)
        rows.setdefault(key, {})[segment] = calc_func(group_data, segment)

    output = []
    for key, seg_data in rows.items():
        row = dict(zip(proj_cols, key))
        for segment, details in seg_data.items():
            for metric, bucket in details.items():
                # e.g. Flat_rate_range_unit_sold, Flat_rate_range_total_sales ...
                row[f"{segment}_rate_range_{metric}"] = bucket
        output.append(row)

    return pd.DataFrame(output)


def process_age_ranges(
    df: pd.DataFrame,
    group_cols: list,
    calc_func,
    proj_cols: list,
) -> pd.DataFrame:
    """
    Build a wide age-range DataFrame by calling calc_func per group.
    Mirrors process_rate_ranges — same shape, different stat source.

    proj_cols : the key columns (e.g. ['index', 'project_name'])
    """
    if df.empty:
        return pd.DataFrame(columns=proj_cols)

    rows = {}
    for (*key_parts, segment), group_data in df.groupby(group_cols):
        key = tuple(key_parts)
        rows.setdefault(key, {})[segment] = calc_func(group_data, segment)

    output = []
    for key, seg_data in rows.items():
        row = dict(zip(proj_cols, key))
        for segment, details in seg_data.items():
            for metric, bucket in details.items():
                row[f"{segment}_age_range_{metric}"] = bucket
        output.append(row)

    return pd.DataFrame(output)


def get_project_type(types) -> str:
    """Aggregate property types into a single project-level label."""
    unique = set(types)
    if "Residential" in unique and "Commercial" in unique:
        return "Residential + Commercial"
    if "Residential" in unique:
        return "Residential"
    if "Commercial" in unique:
        return "Commercial"
    return "Other"


def clean_empty_values(df: pd.DataFrame) -> pd.DataFrame:
    """
    Replace cells containing 0 or empty dict {} with NaN
    so they appear blank in Excel output.
    """
    def _is_empty(val):
        if val is None:
            return True
        if isinstance(val, dict) and len(val) == 0:
            return True
        if isinstance(val, float) and val == 0.0:
            return True
        if isinstance(val, int) and val == 0:
            return True
        return False

    for col in df.columns:
        df[col] = df[col].apply(lambda x: None if _is_empty(x) else x)

    return df