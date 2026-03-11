"""
config.py
=========
All project-wide constants. Import from here — never hardcode elsewhere.
"""

import numpy as np
import pandas as pd

import os
from dotenv import load_dotenv

load_dotenv()  # reads .env from project root

# ── Floor mapping ─────────────────────────────────────────────────────────────
FLOOR_MAP = {
    "Stilt": 0, "Ground": 0, "Habitable": 0, "Upper Ground": 1,
    "Lower Ground": 0, "Upper Floor": 1, "Ground & 1": 0, "Top Floor": 1,
    "Mezzanine Floor": 0, "Basement": 0, "Podium": 0, "Stilt & 3": 3,
    "Stilt & 2": 2, "Ground & 2": 2, "Ground & 5": 0, "Lower Floor": 0,
    "Parking": 0, "Ground & Upper Ground": 0, "Ground & Mezzanine": 0,
    "Terrace": np.nan, "Ground & 2 Upper Floors": 0, "Stilt Ground": 0,
    "Upper Level": 0, "Lower Level": 0, "Ground Floor": 0, "Ground & Upper": 0,
    "Ground & 3": 0, "Ground & 1 & 2": 0, "Higher Ground": 0,
    "Ground & Stilt & 1": 0, "Upper Stilt": 0, "Ground & Stilt": 0,
    "Upper Parking Floor": 0, "Basement & Parking & Upper Floor": 0,
    "Uppper Ground": 1, "Ground & Upper Floor": 0, "Podium Ground": 0,
    "Plaza Floor": 0, "Ground & Upper 3 Floor": 0, "Stilt & Basement": 0,
    "Stilt & Ground": 0, "Ground & One Upper": 0, "Semi Upper": 0,
    "Ground To 2": 0, "4 & 5": 4, "1 & 2": 1, "Gorund": 0,
    "Podium Ground Floor": 0, "G & 3": 0, "5 & 6": 5, "2 & 3": 2,
    "6 & 7": 6, "Stilt & 1": 0, "Ground & Upper Gound": 0,
    "Parking Floor": 0, "Stilt Floor": 0, "Stilt & 9": 9,
    "2,3 & Terrace": 2, "35 Lower & 36 Upper": 35, "P & 1": 0,
    "Groun & 1": 0, "Plasma Floor": 0, "P1": 0,'Terrace Floor':0
}

# ── Loading factors ───────────────────────────────────────────────────────────
# ── Loading factors (Global Defaults) ────────────────────────────────────────
RESIDENTIAL_LOADING = 1.35
COMMERCIAL_LOADING  = 1.40

# ── Per city loading factor overrides ────────────────────────────────────────
CITY_LOADING = {
    "Mumbai": {
        "RESIDENTIAL_LOADING" : 1.45,
        "COMMERCIAL_LOADING"  : 1.50,
    },
    "Pune": {
        "RESIDENTIAL_LOADING" : 1.35,
        "COMMERCIAL_LOADING"  : 1.40,
    },
    "Thane": {
        "RESIDENTIAL_LOADING" : 1.40,
        "COMMERCIAL_LOADING"  : 1.45,
    },
    "Dubai": {
        "RESIDENTIAL_LOADING" : 1.00,   # Dubai uses actual area, no loading
        "COMMERCIAL_LOADING"  : 1.00,
    },
}

def get_city_loading(city: str) -> dict:
    """
    Returns loading factors for a given city.
    Falls back to global defaults if city not defined.
    """
    defaults = {
        "RESIDENTIAL_LOADING" : RESIDENTIAL_LOADING,
        "COMMERCIAL_LOADING"  : COMMERCIAL_LOADING,
    }
    overrides = CITY_LOADING.get(city, {})
    return {**defaults, **overrides}

# ── Property type classification ──────────────────────────────────────────────
RESIDENTIAL_TYPES = [
    "Flat", "Apartment", "Room", "Bunglow", "Studio", "Hall",'Unit',
    "Duplex", "Flat/Shop", "Row_House", "Penthouse", "Triplex Apartment",
]
COMMERCIAL_TYPES = [
    "Shop", "Office", "Commercial Unit", "Commercial", "Commerical", "Showroom",
]

# ── Price range step ──────────────────────────────────────────────────────────
PRICE_STEP = 2_000_000  # 20 Lakhs
AREA_STEP = 200
RATE_STEP = 1000
AGE_INTERVAL = 5

# ── MIN AND MAX RANGES ──────────────────────────────────────────────────────────── 
# ── RATE ──────────────────────────────────────────────────────────────────────────
MIN_RATE = 2000
MAX_RATE = 40000
# ── AREA ──────────────────────────────────────────────────────────────────────────
MIN_AREA = 200
MAX_AREA = 6200
# ── PRICE ─────────────────────────────────────────────────────────────────────────
MIN_PRICE = 500000
MAX_PRICE = 20000000
# ── AGE ───────────────────────────────────────────────────────────────────────────
AGE_MIN      = 25
AGE_MAX      = 55
# ── BHK exclusions ────────────────────────────────────────────────────────────────
NON_BHK_VALUES = ["Shop", "Office", "Others"]

# ── DA Keywords ───────────────────────────────────────────────────────────────────
DA_KEYWORDS =  ['डेव्हलपमेंट अँग्रीमेंट','विकसनकरारनामा','विकसन हक्काचे तबदीलपत्र']

# ── PER CITY RANGE OVERRIDES ──────────────────────────────────────────────────
# Only define what differs from global defaults above.
# Any key not listed falls back to the global MIN_RATE, MAX_RATE etc.

CITY_RANGES = {
    "Mumbai": {
        "MIN_RATE"  : 2000,
        "MAX_RATE"  : 40000,
        "MIN_AREA"  : 200,
        "MAX_AREA"  : 5000,
        "MIN_PRICE" : 500000,
        "MAX_PRICE" : 50000000,
    },
    "Pune": {
        "MIN_RATE"  : 2000,
        "MAX_RATE"  : 40000,
        "MIN_AREA"  : 200,
        "MAX_AREA"  : 4000,
        "MIN_PRICE" : 500000,
        "MAX_PRICE" : 15000000,
    },
    "Thane": {
        "MIN_RATE"  : 2000,
        "MAX_RATE"  : 40000,
        "MIN_AREA"  : 200,
        "MAX_AREA"  : 4500,
        "MIN_PRICE" : 500000,
        "MAX_PRICE" : 20000000,
    },
    "Dubai": {
        "MIN_RATE"  : 1000,
        "MAX_RATE"  : 4000,
        "MIN_AREA"  : 300,
        "MAX_AREA"  : 1000,
        "MIN_PRICE" : 200000,
        "MAX_PRICE" : 10000000,
    },
}

def get_city_ranges(city: str) -> dict:
    """
    Returns range config for a given city.
    Falls back to global defaults for any key not defined.
    """
    defaults = {
        "MIN_RATE"  : MIN_RATE,
        "MAX_RATE"  : MAX_RATE,
        "MIN_AREA"  : MIN_AREA,
        "MAX_AREA"  : MAX_AREA,
        "MIN_PRICE" : MIN_PRICE,
        "MAX_PRICE" : MAX_PRICE,
    }
    overrides = CITY_RANGES.get(city, {})
    return {**defaults, **overrides}


DB_CONFIG = {
    "host"    : os.getenv("DB_HOST", "localhost").strip().strip('"'),
    "port"    : int(str(os.getenv("DB_PORT", 5432)).strip().strip('"')),
    "dbname"  : os.getenv("DB_NAME", "").strip().strip('"'),
    "user"    : os.getenv("DB_USER", "").strip().strip('"'),
    "password": str(os.getenv("DB_PASSWORD", "")).strip().strip('"'),
}

# Table names
DB_CITIES_TABLE       = "city"            # columns: city_id, city_name (at minimum)
DB_TRANSACTIONS_TABLE = "property_transaction_db1"   # columns: city_id + all transaction cols


# -----------------------------------------------------------------------------
# output configuration
# -----------------------------------------------------------------------------

# if ``True`` the merged DataFrames are written to PostgreSQL tables instead of
# Excel files.  You can enable this by setting `SAVE_TO_DB=1` or `SAVE_TO_DB=true`
# in your .env or environment; the default is False so the existing Excel
# behaviour is unchanged.
SAVE_TO_DB = os.getenv("SAVE_TO_DB", "false").lower() in ("1", "true", "yes")

# when saving to the database we use these table names; they can be whatever
# makes sense on the PG side and will be created automatically by pandas.
DB_OUTPUT_TABLES = {
    "project":  "project_merged",
    "location": "location_merged",
    "city":     "city_merged",
}