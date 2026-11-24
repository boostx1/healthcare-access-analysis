PRAGMA foreign_keys = ON;

CREATE TABLE country (
    iso3 TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    peer_group TEXT NOT NULL
);
CREATE TABLE year_dim (year INTEGER PRIMARY KEY);
CREATE TABLE indicator (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    unit TEXT NOT NULL
);
-- A source can explicitly report a missing observation. Keep the NULL and
-- distinguish it from an absent source row when checking the reporting grid.
CREATE TABLE observation (
    iso3 TEXT NOT NULL REFERENCES country(iso3),
    year INTEGER NOT NULL REFERENCES year_dim(year),
    code TEXT NOT NULL REFERENCES indicator(code),
    value REAL CHECK (value IS NULL OR value >= 0),
    PRIMARY KEY (iso3, year, code),
    CHECK (code <> 'SH.XPD.OOPC.CH.ZS' OR value <= 100)
);
