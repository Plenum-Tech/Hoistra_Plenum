# Phase 2 Energy — demo meter CSVs

Upload these via the **Energy** page (`/energy` → Upload meter CSV) or:

```bash
curl -X POST http://localhost:8009/api/energy/readings/ingest/csv \
  -F "file=@meter_anomaly.csv"
```

| File | Purpose |
|------|---------|
| `meter_normal.csv` | Clean half-hourly week (MPAN `1200034567890`) |
| `meter_anomaly.csv` | Weekend spike + baseline drift + Friday asset spike |
| `meter_gaps.csv` | Same as normal with 4 missing half-hours (gap flag) |

Columns: `mpan,reading_at,consumption_kwh`
