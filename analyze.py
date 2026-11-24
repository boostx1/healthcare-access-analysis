"""Build the offline dashboard, SQLite database, and stakeholder outputs."""
import argparse
import csv
import hashlib
import html
import json
import math
import sqlite3
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BEDS = 'SH.MED.BEDS.ZS'
FINANCE = 'SH.XPD.OOPC.CH.ZS'


def read_number(raw):
    if raw is None or str(raw).strip() == '':
        return None
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f'Invalid number {raw!r}')
    return value


def load_data(db, manifest):
    """Check source identity, unpivot the finance matrix and retain source nulls."""
    for name, source in manifest['files'].items():
        digest = hashlib.sha256((ROOT / 'data' / name).read_bytes()).hexdigest()
        if digest != source['sha256']:
            raise ValueError(f'Snapshot checksum mismatch for {name}')
    groups = manifest['country_groups']
    start, end = manifest['period']
    years = range(start, end + 1)
    db.executemany('INSERT INTO year_dim VALUES (?)', [(year,) for year in years])
    db.executemany('INSERT INTO indicator VALUES (?, ?, ?)', [
        (BEDS, 'Hospital bed supply', 'beds per 1,000 people'),
        (FINANCE, 'Household out-of-pocket share', '% of current health expenditure'),
    ])
    names = {}
    facts = []
    with (ROOT / 'data/household_financing.csv').open(newline='', encoding='utf-8') as file:
        for row in csv.DictReader(file):
            code = row['Country Code'].strip()
            if row['Indicator Code'] != FINANCE or code not in groups:
                raise ValueError('Unexpected financing indicator or country')
            name = row['Country Name'].strip()
            db.execute('INSERT INTO country VALUES (?, ?, ?)', (code, name, groups[code]))
            names[code] = name
            for year in years:
                facts.append((code, year, FINANCE, read_number(row[str(year)])))
    meta, records = json.loads((ROOT / 'data/hospital_beds.json').read_text())
    if meta['pages'] != 1 or meta['total'] != len(records) or meta['lastupdated'] != manifest['source_update']:
        raise ValueError('Bed-series metadata does not match the snapshot')
    for row in records:
        code = row['countryiso3code']
        if row['indicator']['id'] != BEDS or row['country']['value'].strip() != names.get(code):
            raise ValueError('Unexpected bed-series indicator or country')
        facts.append((code, int(row['date']), BEDS, read_number(row['value'])))
    db.executemany('INSERT INTO observation VALUES (?, ?, ?, ?)', facts)
    expected = len(groups) * len(years) * 2
    if len(facts) != expected or set(names) != set(groups):
        raise ValueError('Incomplete source grid')
    if db.execute('PRAGMA foreign_key_check').fetchall() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Database integrity check failed')


def query_rows(db, sql):
    return [dict(row) for row in db.execute(sql)]


def bar(value, maximum, kind):
    if value is None:
        return '<span class="missing">Missing</span>'
    unit = '%' if kind == 'share' else ''
    return (f'<span class="value">{value:.2f}{unit}</span>'
            f'<span aria-hidden="true" class="track {kind}"><i style="width:{100 * value / maximum:.2f}%"></i></span>')


def middle(rows, key):
    return statistics.median(row[key] for row in rows) if rows else None


def write_dashboard(path, manifest, panel, quality):
    by_year = defaultdict(list)
    for row in panel:
        by_year[row['year']].append(row)
    latest = quality['latest_complete_year']
    bed_max = max(1, math.ceil(max(r['beds_per_1000'] for r in panel if r['beds_per_1000'] is not None)))
    share_max = max(5, 5 * math.ceil(max(r['oop_share_pct'] for r in panel if r['oop_share_pct'] is not None) / 5))
    options = []
    panels = []
    for year in sorted(by_year, reverse=True):
        rows = by_year[year]
        paired = [r for r in rows if r['beds_per_1000'] is not None and r['oop_share_pct'] is not None]
        bed_median = middle(paired, 'beds_per_1000')
        oop_median = middle(paired, 'oop_share_pct')
        selected = ' selected' if year == latest else ''
        hidden = '' if year == latest else ' hidden'
        options.append(f'<option value="{year}"{selected}>{year}</option>')
        cells = ''.join(f'<tr><th scope="row">{html.escape(r["country"])}</th>'
                        f'<td>{bar(r["beds_per_1000"], bed_max, "beds")}</td>'
                        f'<td>{bar(r["oop_share_pct"], share_max, "share")}</td></tr>' for r in rows)
        metrics = [
            (f'{len(paired)} / {len(rows)}', 'Countries reporting both measures'),
            ('—' if bed_median is None else f'{bed_median:.2f}', 'Median beds per 1,000 people'),
            ('—' if oop_median is None else f'{oop_median:.2f}%', 'Median household spending share'),
        ]
        cards = ''.join(f'<div class="metric"><b>{v}</b><span>{label}</span></div>' for v, label in metrics)
        panels.append(f'''<section data-year="{year}" aria-label="{year} results"{hidden}>
<div class="metrics">{cards}</div><p class="note">Medians give equal weight to each country with both values in {year}. Missing values are left blank in the data export.</p>
<div class="tablebox"><table><caption>Reported values in {year}</caption>
<thead><tr><th scope="col">Country</th><th scope="col">Hospital beds per 1,000 people</th><th scope="col">Out-of-pocket share of health spending</th></tr></thead>
<tbody>{cells}</tbody></table></div><p class="note">Bars start at zero. Full scales are {bed_max} beds per 1,000 people and {share_max}% of spending, fixed across years.</p></section>''')
    template = (ROOT / 'templates/dashboard.html').read_text()
    page = template.replace('{{PANELS}}', '\n'.join(panels)).replace('{{OPTIONS}}', ''.join(options))
    path.write_text(page.replace('{{UPDATED}}', manifest['source_update']), encoding='utf-8')


def write_brief(path, db, latest, panel):
    current = {row['iso3']: row for row in panel if row['year'] == latest}
    uae, sweden = current['ARE'], current['SWE']
    changes = {r['iso3']: r for r in query_rows(db, 'SELECT * FROM common_period_change')}
    change = changes['ARE']
    year_after = latest + 1
    next_coverage = db.execute('SELECT complete_pairs FROM annual_coverage WHERE year = ?', (year_after,)).fetchone()
    coverage_sentence = (f' In {year_after} only {next_coverage[0]} of the {len(current)} countries had both measures.'
                         if next_coverage else '')
    gap = sweden['oop_share_pct'] - uae['oop_share_pct']
    largest = max(current.values(), key=lambda r: r['beds_per_1000'])
    smallest = min(current.values(), key=lambda r: r['beds_per_1000'])
    path.write_text(f'''# Brief for a provider partnerships team

## Which comparisons belong in a partner discussion?

Within the {min(r["year"] for r in panel)}–{max(r["year"] for r in panel)} study window, use {latest} for a common-year comparison of the six countries.{coverage_sentence} Mixing the newest value from each country would put different reporting years side by side.

| {latest} national indicator | United Arab Emirates | Sweden | Six-country median |
| --- | ---: | ---: | ---: |
| Hospital beds per 1,000 people | {uae['beds_per_1000']:.2f} | {sweden['beds_per_1000']:.2f} | {middle(current.values(), 'beds_per_1000'):.2f} |
| Out-of-pocket share of current health spending | {uae['oop_share_pct']:.2f}% | {sweden['oop_share_pct']:.2f}% | {middle(current.values(), 'oop_share_pct'):.2f}% |

The two countries reported similar bed supply relative to population. Their household financing shares differed by {abs(gap):.2f} percentage points, with the higher share in {'Sweden' if gap > 0 else 'the UAE'}. These shares describe how each health system was financed and cannot be converted into a price for an international patient.

Across the six countries, bed supply ranged from {smallest['beds_per_1000']:.2f} in {smallest['country']} to {largest['beds_per_1000']:.2f} in {largest['country']}. Between {change['first_year']} and {latest}, the first and last years with complete six-country coverage, UAE bed density changed by {change['beds_change']:+.2f} beds per 1,000 and its out-of-pocket share changed by {change['oop_change_pp']:+.2f} percentage points. The indicators alone cannot explain those changes.

## What to ask the provider

For a partner offering a particular procedure, request staffed beds for the service, occupied bed-days and available staffed bed-days over the same month, and the median time from referral to treatment. For price comparisons, request the same procedure and inclusion criteria plus what an international patient must pay directly. These inputs would allow a follow-up analysis of provider capacity, delays and prices.

## Reading the figures

The median gives each selected country equal weight. These six countries are a purposive comparison group. National bed definitions and service delivery models can differ. Neither measure captures patient outcomes, quality, occupancy or the availability of an appointment. The inputs contain public, annual national aggregates.

Source data from WHO and World Bank, World Development Indicators. [Bed density](https://data.worldbank.org/indicator/SH.MED.BEDS.ZS) · [Household financing](https://data.worldbank.org/indicator/SH.XPD.OOPC.CH.ZS) · [Data licence](https://creativecommons.org/licenses/by/4.0/).
''', encoding='utf-8')


def build(output):
    manifest = json.loads((ROOT / 'data/sources.json').read_text())
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temp:
        stage = Path(temp)
        for folder in ('build', 'docs', 'reports'):
            (stage / folder).mkdir()
        db = sqlite3.connect(stage / 'build/healthcare.sqlite')
        db.row_factory = sqlite3.Row
        try:
            db.executescript((ROOT / 'sql/schema.sql').read_text())
            load_data(db, manifest)
            db.executescript((ROOT / 'sql/analysis.sql').read_text())
            panel = query_rows(db, 'SELECT * FROM country_year ORDER BY year, country')
            coverage = query_rows(db, 'SELECT * FROM annual_coverage ORDER BY year')
            common = [r['year'] for r in coverage if r['expected_pairs'] == r['complete_pairs']]
            if not common:
                raise ValueError('No year has complete peer coverage')
            missing = query_rows(db, 'SELECT iso3, year, code FROM observation WHERE value IS NULL ORDER BY iso3, year, code')
            quality = {
                'panel_country_years': len(panel),
                'source_observations': db.execute('SELECT COUNT(*) FROM observation').fetchone()[0],
                'complete_pairs': sum(r['complete_pairs'] for r in coverage),
                'latest_complete_year': max(common), 'complete_years': common,
                'missing_observations': missing, 'annual_coverage': coverage,
                'passed_checks': ['snapshot SHA-256', 'indicator and country identity', 'complete source grid',
                                  'unique country-year-indicator keys', 'foreign keys', 'value ranges', 'database integrity'],
            }
            (stage / 'reports/quality.json').write_text(json.dumps(quality, indent=2) + '\n')
            with (stage / 'reports/country_year.csv').open('w', newline='', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=panel[0].keys(), lineterminator='\n')
                writer.writeheader()
                writer.writerows(panel)
            write_dashboard(stage / 'docs/index.html', manifest, panel, quality)
            write_brief(stage / 'reports/provider-brief.md', db, max(common), panel)
            db.commit()
        finally:
            db.close()
        for folder in ('build', 'docs', 'reports'):
            (output / folder).mkdir(exist_ok=True)
            for artifact in (stage / folder).iterdir():
                artifact.replace(output / folder / artifact.name)
    return quality


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT, help='Output directory (default: project root)')
    quality = build(parser.parse_args().out.resolve())
    print(f"Built {quality['panel_country_years']} country-years, {quality['complete_pairs']} complete pairs. "
          f"Latest fully comparable year: {quality['latest_complete_year']}.")
