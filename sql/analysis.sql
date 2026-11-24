-- Build the full country-year panel before joining the two series, so a
-- missing bed count never suppresses an available financing observation.
CREATE VIEW country_year AS
SELECT c.iso3, c.name AS country, c.peer_group, y.year,
       b.value AS beds_per_1000, f.value AS oop_share_pct
FROM country AS c CROSS JOIN year_dim AS y
LEFT JOIN observation AS b ON b.iso3 = c.iso3 AND b.year = y.year
    AND b.code = 'SH.MED.BEDS.ZS'
LEFT JOIN observation AS f ON f.iso3 = c.iso3 AND f.year = y.year
    AND f.code = 'SH.XPD.OOPC.CH.ZS';

CREATE VIEW annual_coverage AS
SELECT year, COUNT(*) AS expected_pairs, COUNT(beds_per_1000) AS bed_values,
       COUNT(oop_share_pct) AS financing_values,
       COUNT(CASE WHEN beds_per_1000 IS NOT NULL AND oop_share_pct IS NOT NULL
                  THEN 1 END) AS complete_pairs
FROM country_year GROUP BY year;

CREATE VIEW comparable_years AS
SELECT year FROM annual_coverage WHERE complete_pairs = expected_pairs;

-- Endpoint differences use the same calendar years for every country. A change
-- in the household financing share has units of percentage points.
CREATE VIEW common_period_change AS
SELECT a.iso3, a.year AS first_year, z.year AS last_year,
       z.beds_per_1000 - a.beds_per_1000 AS beds_change,
       z.oop_share_pct - a.oop_share_pct AS oop_change_pp
FROM country_year AS a JOIN country_year AS z ON z.iso3 = a.iso3
WHERE a.year = (SELECT MIN(year) FROM comparable_years)
  AND z.year = (SELECT MAX(year) FROM comparable_years);
