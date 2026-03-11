
"""
aggregators/project.py
======================
Project-wise aggregation pipeline.
Now flexible for:
- Project
- Project × Year (YoY)
- Project × Year × Quarter (QoQ)
"""

import pandas as pd

from aggregators.base import (
    build_masks,
    create_pivot,
    apply_and_merge,
    process_price_ranges,
    process_rate_ranges,
    process_age_ranges,           # ← new
    get_project_type,
    clean_empty_values
)

from stats.rate import (
    most_prevailing_rate_on_ca,
    create_rate_ranges,
    floor_wise_wrapper,
    create_rate_range_stats_by_property_type,   # ← new
    create_rate_range_stats_by_bhk,             # ← new
)

from stats.area import (
    create_area_ranges,
    create_area_ranges_sales,
    create_area_ranges_avg_sales,
    create_area_ranges_area,
    create_area_ranges_unit_sold,
    create_area_ranges_unit_sales,
    create_area_ranges_unit_avg_sales,
    create_area_ranges_unit_carpet_area_consumed
)

from stats.price import (
    calculate_property_type_price_range,
    calculate_bhk_price_range,
)

from stats.buyer import get_project_pincode_stats

from stats.age import (           # ← new
    create_age_range_stats_by_property_type,
    create_age_range_stats_by_bhk,
)

from config import PRICE_STEP, AREA_STEP, RATE_STEP

from preprocessing import round_dict_floats
# ============================================================
# FLEXIBLE AGGREGATION ENGINE
# ============================================================

def build_project_aggregation(
    dataframe: pd.DataFrame,
    group_cols: list,
    base_col: str,
    prevailing_percentile: int = 90,
    prevailing_band: float = 0.05,
    
) -> pd.DataFrame:
    """
    Generic aggregation engine.
    """

    # 🔹 Dynamic grouping
    PT_GROUPS  = group_cols + ["property_type"]
    BHK_GROUPS = group_cols + ["bhk_br"]

    dataframe = dataframe[(dataframe["manual_processed"] == "Yes")&(dataframe['property_category']=='Sale')].copy()
    # guard against missing columns which older datasets sometimes lack
    if "location_id" not in dataframe.columns and "loc_id" in dataframe.columns:
        dataframe["location_id"] = dataframe["loc_id"]
    if "property_type" not in dataframe.columns:
        dataframe["property_type"] = "Other"

    dataframe['rate_on_net_ca']=dataframe['rate_on_net_ca'].astype(float)
    dataframe['agreement_price']=dataframe['agreement_price'].astype(float)

    print("data in dataframe ", dataframe.shape)

    m = build_masks(dataframe, base_col="proj_id")

    print("=== Analysis Masks Summary ===")
    print(f"Base (all transactions)  : {m['base'].sum()}")
    print(f"Valid price              : {(m['base'] & m['valid_price']).sum()}")
    print(f"Valid area               : {(m['base'] & m['valid_area']).sum()}")
    print(f"Valid rate               : {(m['base'] & m['valid_rate']).sum()}")

    # ── Convenience slices ───────────────────────────────────
    base_df         = dataframe[m["base"]]
    rate_mask       = m["base"] & m["valid_rate"]
    bhk_mask        = m["bhk_base"]
    bhk_rate_mask   = bhk_mask & m["valid_rate"]

    # ========================================================
    # BASE AGGREGATION
    # ========================================================
    project_wise_summary = (
        base_df
        .groupby(group_cols)
        .agg(
            index                               =("index","first"),
            project                             =('project_name',"first"),
            location                            =("location", "first"),
            location_id                         =("location_id",             "first"),
            # igr_village_list                    =('igr_village', lambda x:list(x.unique())),
            project_lat                         =("project_lat",             "first"),
            project_lng                         =("project_lng",             "first"),
            city                                =("city",                    "first"),
            city_id                             =("city_id",                 "first"),
            total_sales                         =("agreement_price",         "sum"),
            total_ca_consumed_sqft_igr          =("carpet_sqft",            "sum"),
            total_transactions                  =("document_no",             "count"),
            max_floor                           =("floor_no",                "max"),
            recent_transaction_date             =("transaction_date",        "max"),
            project_type                        =("project_type",            get_project_type),
        )
        .reset_index()
    )

    print(f"\nAggregated rows: {len(project_wise_summary)}")

    # ========================================================
    # PROPERTY TYPE & BHK PIVOTS
    # ========================================================

    pivots = [
        # Property-type
        (dataframe[m["base"]],                    PT_GROUPS,  "document_no",         "count", "_sold_igr"),
        (dataframe[m["base"]],                    PT_GROUPS,  "agreement_price",     "sum",   "_total_agreement_price"),
        (dataframe[m["base"] & m["valid_price"]], PT_GROUPS,  "agreement_price",     "mean",  "_avg_agreement_price"),
        (dataframe[m["base"]],                    PT_GROUPS,  "carpet_sqft",         "sum",   "_ca_consumed_sqft_igr"),

        # BHK
        (dataframe[bhk_mask],                    BHK_GROUPS, "document_no",         "count", "_sold_igr"),
        (dataframe[bhk_mask],                    BHK_GROUPS, "agreement_price",     "sum",   "_total_agreement_price"),
        (dataframe[bhk_mask & m["valid_price"]], BHK_GROUPS, "agreement_price",     "mean",  "_avg_agreement_price"),
        (dataframe[bhk_mask],                    BHK_GROUPS, "carpet_sqft",         "sum",   "_ca_consumed_sqft_igr"),
    ]

    for src, gcols, vcol, agg, sfx in pivots:
        project_wise_summary = project_wise_summary.merge(
            create_pivot(src, gcols, vcol, agg, sfx),
            on=group_cols,
            how="left",
        )

    # ========================================================
    # RATE CALCULATIONS — PROPERTY TYPE
    # ========================================================

    if rate_mask.sum() > 0:
        rate_df = dataframe[rate_mask]

        # Net Carpet Area Rate
        proj_rate_nca = (
            rate_df
            .groupby(PT_GROUPS)["rate_on_net_ca"]
            .agg(
                wt_avg_rate_nca="mean",
                p50_rate_nca=lambda x: x.quantile(0.5),
                p75_rate_nca=lambda x: x.quantile(0.75),
                p90_rate_nca=lambda x: x.quantile(0.9),
            )
            .reset_index()
            .pivot(index=group_cols, columns="property_type")
        )
        proj_rate_nca.columns = [f"{c[1]}_{c[0]}" for c in proj_rate_nca.columns]
        proj_rate_nca = proj_rate_nca.round(2)
        project_wise_summary = project_wise_summary.merge(
            proj_rate_nca.reset_index(),
            on=group_cols,
            how="left",
        )

        # Saleable Area Rate
        proj_rate_sa = (
            rate_df
            .groupby(PT_GROUPS)["rate_on_sa"]
            .agg(
                wt_avg_rate_sa="mean",
                p50_rate_sa=lambda x: x.quantile(0.5),
                p75_rate_sa=lambda x: x.quantile(0.75),
                p90_rate_sa=lambda x: x.quantile(0.9),
            )
            .reset_index()
            .pivot(index=group_cols, columns="property_type")
        )
        proj_rate_sa.columns = [f"{c[1]}_{c[0]}" for c in proj_rate_sa.columns]
        proj_rate_sa = proj_rate_sa.round(2)
        project_wise_summary = project_wise_summary.merge(
            proj_rate_sa.reset_index(),
            on=group_cols,
            how="left",
        )

        # Floor-wise 90P Rate
        project_wise_summary = project_wise_summary.merge(
            apply_and_merge(rate_df, PT_GROUPS, floor_wise_wrapper, "_floor_wise_90p_rate"),
            on=group_cols,
            how="left",
        )

        # Prevailing rate — now configurable
        project_wise_summary = project_wise_summary.merge(
            apply_and_merge(
                rate_df,
                PT_GROUPS,
                lambda g, seg: most_prevailing_rate_on_ca(
                    g,
                    seg,
                    percentile=prevailing_percentile,
                    band_pct=prevailing_band,
                ),
                "_most_prevailing_rate_range",
            ),
            on=group_cols,
            how="left",
        )

        # Rate range distribution (unchanged)
        project_wise_summary = project_wise_summary.merge(
            apply_and_merge(
                rate_df,
                PT_GROUPS,
                lambda g, seg: create_rate_ranges(
                    g,
                    seg,
                    bin_strategy="mean",
                    interval=RATE_STEP,   # or your desired interval
                ),
                "_total_unit_sold_in_rate_range",
            ),
            on=group_cols,
            how="left",
        )

    # ========================================================
    # RATE CALCULATIONS — BHK
    # ========================================================

    if bhk_rate_mask.sum() > 0:
        bhk_rate = (
            dataframe[bhk_rate_mask]
            .groupby(BHK_GROUPS)["rate_on_net_ca"]
            .agg(
                wt_avg_rate_nca="mean",
                p50_rate_nca=lambda x: x.quantile(0.5),
                p75_rate_nca=lambda x: x.quantile(0.75),
                p90_rate_nca=lambda x: x.quantile(0.9),
            )
            .reset_index()
            .pivot(index=group_cols, columns="bhk_br")
        )
        bhk_rate.columns = [f"{c[1]}_{c[0]}" for c in bhk_rate.columns]
        bhk_rate = bhk_rate.round(2) 
        project_wise_summary = project_wise_summary.merge(
            bhk_rate.reset_index(),
            on=group_cols,
            how="left",
        )

    # ========================================================
    # AREA RANGES — PROPERTY TYPE
    # ========================================================

    area_mask       = m["base"] & m["valid_area"]
    area_price_mask = m["base"] & m["valid_price"] & m["valid_area"]

    if area_mask.sum() > 0:
        project_wise_summary = project_wise_summary.merge(
            apply_and_merge(
                dataframe[area_mask],
                PT_GROUPS,
                lambda g, seg: create_area_ranges(
                    g,
                    seg,
                    bin_strategy="mean",
                    interval=AREA_STEP,
                ),
                "_total_unit_sold_in_area_range",
            ),
            on=group_cols,
            how="left",
        )

    if area_price_mask.sum() > 0:
        for func, sfx in [
        (create_area_ranges_sales,     "_total_agreement_price_in_area_range"),
        (create_area_ranges_avg_sales, "_avg_agreement_price_in_area_range"),]:
            project_wise_summary = project_wise_summary.merge(
            apply_and_merge(
                dataframe[area_price_mask],
                PT_GROUPS,
                lambda g, seg, f=func: f(
                    g,
                    seg,
                    bin_strategy="mean",
                    interval=AREA_STEP,
                ),
                sfx,
            ),
            on=group_cols,
            how="left",
        )
            
    project_wise_summary = project_wise_summary.merge(
            apply_and_merge(
                dataframe[area_mask],
                PT_GROUPS,
                create_area_ranges_area,
                "_total_ca_consumed_in_area_range_sqft", 
            ),
            on=group_cols,
            how="left",
        )
    # ========================================================
    # AREA RANGES — BHK
    # ========================================================

    bhk_carpet_mask = bhk_mask & m["valid_carpet"]
    print(dataframe[bhk_carpet_mask].shape,": dataframe[bhk_carpet_mask]")
    print("BHK_GROUPS: ",BHK_GROUPS)

    if bhk_carpet_mask.sum() > 0:
        project_wise_summary = project_wise_summary.merge(
            apply_and_merge(
        dataframe[bhk_carpet_mask],
        BHK_GROUPS,
        lambda g, seg: create_area_ranges_unit_sold(
            g,
            seg,
            bin_strategy="mean",
            interval=AREA_STEP,
        ),
        "_total_unit_sold_in_area_range",),
        on=group_cols,
        how="left",
        )

        project_wise_summary = project_wise_summary.merge(
            apply_and_merge(
        dataframe[bhk_carpet_mask],
        BHK_GROUPS,
        lambda g, seg: create_area_ranges_unit_carpet_area_consumed(
            g,
            seg,
            bin_strategy="mean",
            interval=AREA_STEP,
        ),
        "_total_ca_consumed_in_area_range_sqft",),
        on=group_cols,
        how="left",
        )

        bhk_carpet_price_mask = bhk_carpet_mask & m["valid_price"]

        if bhk_carpet_price_mask.sum() > 0:
            for func, sfx in [
                (create_area_ranges_unit_sales,     "_total_agreement_price_in_area_range"),
                (create_area_ranges_unit_avg_sales, "_avg_agreement_price_in_area_range"),
            ]:
                project_wise_summary = project_wise_summary.merge(
                    apply_and_merge(
                        dataframe[bhk_carpet_price_mask],
                        BHK_GROUPS,
                        lambda g, seg, f=func: f(
                            g,
                            seg,
                            bin_strategy="mean",
                            interval=AREA_STEP,
                        ),
                        sfx,
                    ),
                    on=group_cols,
                    how="left",
                )

    # ========================================================
    # AVG CARPET AREA — BHK
    # ========================================================

    if bhk_carpet_mask.sum() > 0:
        avg_carpet = (
            dataframe
            .groupby(BHK_GROUPS)["carpet_sqft"]
            .mean()
            .round(2)
            .unstack()
            .add_suffix("_avg_carpet_area_in_sqft")
            .reset_index()
        )
        project_wise_summary = project_wise_summary.merge(
            avg_carpet,
            on=group_cols,
            how="left",
        )

    # ========================================================
    # PRICE RANGES
    # ========================================================

    # ========================================================
# PRICE RANGES
# ========================================================

    price_mask = m["base"] & m["valid_price"]

    if price_mask.sum() > 0:
        project_wise_summary = project_wise_summary.merge(
            process_price_ranges(
                dataframe[price_mask],
                PT_GROUPS,
                lambda g, seg: calculate_property_type_price_range(
                    g,
                    seg,
                    bound_strategy="mean",
                    step=PRICE_STEP,
                ),
                group_cols,
            ),
            on=group_cols,
            how="left",
        )

    if (bhk_mask & m["valid_price"]).sum() > 0:
        project_wise_summary = project_wise_summary.merge(
            process_price_ranges(
                dataframe[bhk_mask & m["valid_price"]],
                BHK_GROUPS,
                lambda g, seg: calculate_bhk_price_range(
                    g,
                    seg,
                    bound_strategy="mean",
                    step=PRICE_STEP
                ),
                group_cols,
            ),
            on=group_cols,
            how="left",
        )

    # ========================================================
    # RATE RANGES — PROPERTY TYPE
    # ========================================================

    if rate_mask.sum() > 0:
        project_wise_summary = project_wise_summary.merge(
            process_rate_ranges(
                dataframe[rate_mask],
                PT_GROUPS,
                lambda g, seg: create_rate_range_stats_by_property_type(
                    g,
                    seg,
                    bin_strategy="mean",
                    interval=RATE_STEP,
                ),
                group_cols,
            ),
            on=group_cols,
            how="left",
        )

    # ========================================================
    # RATE RANGES — BHK
    # ========================================================

    if bhk_rate_mask.sum() > 0:
        project_wise_summary = project_wise_summary.merge(
            process_rate_ranges(
                dataframe[bhk_rate_mask],
                BHK_GROUPS,
                lambda g, seg: create_rate_range_stats_by_bhk(
                    g,
                    seg,
                    bin_strategy="mean",
                    interval=RATE_STEP,
                ),
                group_cols,
            ),
            on=group_cols,
            how="left",
        )


    # ========================================================
    # PINCODE STATS
    # ========================================================

    if price_mask.sum() > 0:
        project_wise_summary = project_wise_summary.merge(
            get_project_pincode_stats(
                dataframe[price_mask],
                group_cols=group_cols,
            ),
            on=group_cols,
            how="left",
        )

    # ========================================================
    # AGE RANGES — PROPERTY TYPE
    # ========================================================

    age_mask = m["base"]   # age is derived in preprocessing, no extra mask needed

    if age_mask.sum() > 0:
        project_wise_summary = project_wise_summary.merge(
            process_age_ranges(
                dataframe[age_mask],
                PT_GROUPS,
                lambda g, seg: create_age_range_stats_by_property_type(
                    g,
                    seg,
                    min_age=25,
                    max_age=55,
                    interval=5,
                ),
                group_cols,
            ),
            on=group_cols,
            how="left",
        )

    # ========================================================
    # AGE RANGES — BHK
    # ========================================================

    if (bhk_mask).sum() > 0:
        project_wise_summary = project_wise_summary.merge(
            process_age_ranges(
                dataframe[bhk_mask],
                BHK_GROUPS,
                lambda g, seg: create_age_range_stats_by_bhk(
                    g,
                    seg,
                    min_age=25,
                    max_age=55,
                    interval=5,
                ),
                group_cols,
            ),
            on=group_cols,
            how="left",
        )


    # ---- Final cleanup ----
    project_wise_summary = project_wise_summary.loc[:, ~project_wise_summary.columns.duplicated()]
    project_wise_summary.columns = project_wise_summary.columns.str.lower()

    # Round plain float columns

    # Round plain float columns
    float_cols = project_wise_summary.select_dtypes(include='float').columns
    float_cols = float_cols.difference(['project_lat', 'project_lng'])
    project_wise_summary[float_cols] = project_wise_summary[float_cols].round(2)
    
    project_wise_summary[float_cols] = project_wise_summary[float_cols].round(2)

    # Round floats inside dict columns
    dict_cols = [
        col for col in project_wise_summary.columns
        if project_wise_summary[col].apply(lambda x: isinstance(x, dict)).any()
    ]
    for col in dict_cols:
        project_wise_summary[col] = project_wise_summary[col].apply(round_dict_floats)
    
    project_wise_summary = clean_empty_values(project_wise_summary)


    print(f"\n=== Final Output ===")
    print(f"Shape: {project_wise_summary.shape}")

    return project_wise_summary


# ============================================================
# WRAPPERS
# ============================================================

def build_project_wise(df: pd.DataFrame) -> pd.DataFrame:
    return build_project_aggregation(df, ["proj_id"],'proj_id')


def build_yoy_project_wise(df: pd.DataFrame) -> pd.DataFrame:
    base = build_project_aggregation(df, ["proj_id", "year"],'proj_id')
    base = base.sort_values(["proj_id", "year"])
    return base

def build_qoq_project_wise(df: pd.DataFrame) -> pd.DataFrame:
    base = build_project_aggregation(df, ["proj_id", "quarter"],'proj_id')
    base = base.sort_values(["proj_id", "quarter"])
    return base