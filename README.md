# Dynastykey — Harris County ArcGIS Scraper

Pulls Harris County property records in bulk from public ArcGIS REST services,
stores them in a local SQLite database, and matches lists of names against the
owner records.

All data sources here are **public records** published by Harris County / HCAD
(Harris Central Appraisal District).

## How it fits together

```
ArcGIS parcels layer  ──┐
(geometry + HCAD acct)  │
                        ├──►  SQLite (data/dynastykey.db)  ──►  name matching
HCAD bulk owner file  ──┘         parcels / owners                (local fuzzy or Claude)
(acct -> owner name)
```

1. **Bulk pull** the parcels layer from the county GIS server (every parcel,
   with its HCAD account number and site address; optionally geometry).
2. **Load owner names** from HCAD's bulk `Real_acct_owner.zip` download
   (account number → owner name + mailing address). This joins to parcels on
   the account number.
3. **Match names** against the database — locally (exact / token-set / fuzzy),
   or with Claude for the messy cases (nicknames, trusts, LLCs).

## Setup

```bash
pip install -r requirements.txt
```

## Usage

### 1. Verify / explore the endpoints

GIS servers move over time, so check what's live first:

```bash
# Browse the county's service catalog
python main.py discover "https://www.gis.hctx.net/arcgishcpid/rest/services"
python main.py discover "https://www.gis.hctx.net/arcgis/rest/services"

# Inspect the parcels layer (fields, count, pagination support)
python main.py discover "https://www.gis.hctx.net/arcgishcpid/rest/services/HCAD/Parcels/FeatureServer/0"
```

### 2. Bulk-pull parcels

```bash
# Test with a small pull first
python main.py pull-parcels --limit 500

# Full pull (~1.5M parcels; takes a while, resumable)
python main.py pull-parcels --resume

# With point coordinates (parcel centroids)
python main.py pull-parcels --geometry --resume

# Any other layer (e.g. address points once you find its URL via discover)
python main.py pull "https://.../FeatureServer/0" --table address_points --geometry
```

Pulls are polite (throttled, retried with backoff) and resumable with
`--resume` if interrupted.

### 3. Load HCAD owner names (bulk)

```bash
python main.py load-owners --year 2025
# or if you downloaded the zip manually from hcad.org:
python main.py load-owners --zip data/Real_acct_owner_2025.zip
```

If the download 404s, grab the current link from
<https://hcad.org/pdata/pdata-property-downloads.html> and pass `--zip`.

### 4. Match names against the database

```bash
python main.py match --name "JOHN SMITH"
python main.py match --names-file leads.txt --csv matches.csv
python main.py match --name "SMITH JOHN" --table parcels --field owner_name
```

Local matching handles exact matches, reordered tokens ("SMITH JOHN" vs
"JOHN SMITH"), and fuzzy near-misses (`--threshold`, default 0.82).

### 5. Claude-assisted matching (optional)

For names a string metric can't judge — nicknames, family trusts, LLCs:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python main.py claude-match --names-file leads.txt --json-out matches.json
```

### Utilities

```bash
python main.py stats            # row counts per table
sqlite3 data/dynastykey.db      # query the data directly
```

Example join (parcel + owner by account number):

```sql
SELECT p.hcad_num, o.mailto AS owner, p.site_addr_1, o.mail_addr_1
FROM parcels p JOIN owners o ON o.acct = p.hcad_num
WHERE o.mailto LIKE '%SMITH JOHN%';
```

## Default endpoints

| Data | Source |
|---|---|
| Parcels (FeatureServer) | `https://www.gis.hctx.net/arcgishcpid/rest/services/HCAD/Parcels/FeatureServer/0` |
| Parcels (MapServer fallback) | `https://www.gis.hctx.net/arcgis/rest/services/HCAD/Parcels/MapServer/0` |
| Bulk owner data | `https://download.hcad.org/data/CAMA/{year}/Real_acct_owner.zip` |

Field names in your local tables are lowercased/sanitized versions of the
layer's field names — run `discover` on the layer or `PRAGMA table_info(parcels)`
in sqlite3 to see them.
