"""Plain-Python pipeline steps. The ADK agents in agent.py call these."""
import html as _html
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

from . import config


def fetch_earthquakes(days=config.DAYS_BACK, min_mag=config.MIN_MAGNITUDE):
    """Ingest: pull recent earthquakes from the USGS public API (GeoJSON)."""
    end = datetime.now(timezone.utc)
    params = {
        "format": "geojson",
        "starttime": (end - timedelta(days=days)).strftime("%Y-%m-%d"),
        "endtime": (end + timedelta(days=1)).strftime("%Y-%m-%d"),
        "minmagnitude": min_mag,
        "orderby": "time",
        "limit": 20000,
    }
    resp = requests.get(config.USGS_URL, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _region(place):
    """'12 km SW of Ridgecrest, CA' -> 'CA'; fall back to the full place text."""
    if not place:
        return "Unknown"
    return place.split(",")[-1].strip() if "," in place else place.strip()


def _bucket(mag):
    if mag < 3: return "2.5-2.9"
    if mag < 4: return "3.0-3.9"
    if mag < 5: return "4.0-4.9"
    if mag < 6: return "5.0-5.9"
    return "6.0+"


BUCKET_ORDER = ["2.5-2.9", "3.0-3.9", "4.0-4.9", "5.0-5.9", "6.0+"]


def process(raw):
    """Transform: clean/filter, then aggregate by day, magnitude band and region."""
    rows, dropped = [], 0
    for f in raw.get("features", []):
        p, g = f.get("properties", {}), f.get("geometry") or {}
        if p.get("mag") is None or p.get("time") is None:  # data-quality filter
            dropped += 1
            continue
        if p.get("type") != "earthquake":  # exclude quarry blasts, explosions, etc.
            dropped += 1
            continue
        coords = g.get("coordinates") or [None, None, None]
        rows.append({
            "time": datetime.fromtimestamp(p["time"] / 1000, timezone.utc),
            "mag": float(p["mag"]),
            "place": p.get("place"),
            "region": _region(p.get("place")),
            "depth_km": coords[2],
        })

    by_day = Counter(r["time"].strftime("%Y-%m-%d") for r in rows)
    by_band = Counter(_bucket(r["mag"]) for r in rows)
    by_region = Counter(r["region"] for r in rows)

    region_mags = {}
    for r in rows:
        region_mags.setdefault(r["region"], []).append(r["mag"])
    top_regions = [
        {"region": k, "count": v, "avg_mag": round(sum(region_mags[k]) / v, 2),
         "max_mag": max(region_mags[k])}
        for k, v in by_region.most_common(10)
    ]
    biggest = sorted(rows, key=lambda r: r["mag"], reverse=True)[:10]
    depths = [r["depth_km"] for r in rows if r["depth_km"] is not None]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": config.DAYS_BACK,
        "min_magnitude": config.MIN_MAGNITUDE,
        "total_events": len(rows),
        "dropped_events": dropped,
        "avg_magnitude": round(sum(r["mag"] for r in rows) / len(rows), 2) if rows else 0,
        "max_magnitude": max((r["mag"] for r in rows), default=0),
        "avg_depth_km": round(sum(depths) / len(depths), 1) if depths else 0,
        "by_day": [{"date": d, "count": by_day[d]} for d in sorted(by_day)],
        "by_magnitude_band": [{"band": b, "count": by_band.get(b, 0)} for b in BUCKET_ORDER],
        "top_regions": top_regions,
        "biggest_events": [
            {"time": r["time"].strftime("%Y-%m-%d %H:%M UTC"), "mag": r["mag"], "place": r["place"]}
            for r in biggest
        ],
    }


def store(name, content, content_type):
    """Storage: always write locally; also upload to GCS when STORAGE_BACKEND=gcs."""
    path = config.OUTPUT_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if config.STORAGE_BACKEND != "gcs":
        return f"local:output/{name}"
    if not config.GCS_BUCKET:
        raise RuntimeError("STORAGE_BACKEND=gcs but GCS_BUCKET is not set")
    from google.cloud import storage  # imported lazily so local mode needs no GCP setup
    blob = storage.Client().bucket(config.GCS_BUCKET).blob(name)
    blob.upload_from_string(content, content_type=content_type)
    return f"gs://{config.GCS_BUCKET}/{name}"


def render_html(summary, insight=""):
    """Visualization: self-contained HTML page (Chart.js via CDN) with charts + table."""
    data = json.dumps(summary).replace("</", "<\\/")
    insight_html = (f'<div class="card insight"><h2>AI insight</h2><p>{_html.escape(str(insight))}</p></div>'
                    if insight else "")
    return HTML_TEMPLATE.replace("__DATA__", data).replace("__INSIGHT__", insight_html)


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Earthquake Activity Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#f4f6f8;color:#1c2733}
 header{background:#1f3a5f;color:#fff;padding:20px 24px}
 header h1{margin:0;font-size:1.5rem} header p{margin:4px 0 0;opacity:.8;font-size:.9rem}
 main{max-width:1100px;margin:0 auto;padding:16px;display:grid;gap:16px}
 .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
 .card{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.1)}
 .kpi .v{font-size:1.8rem;font-weight:700;color:#1f3a5f} .kpi .l{font-size:.8rem;color:#5b6b7b}
 .row{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
 table{width:100%;border-collapse:collapse;font-size:.9rem} th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #e4e9ee}
 h2{margin:0 0 10px;font-size:1.05rem} .insight{border-left:4px solid #2a9d8f}
</style></head><body>
<header><h1>Earthquake Activity Report</h1><p id="sub"></p></header>
<main>
 <div class="kpis" id="kpis"></div>
 __INSIGHT__
 <div class="card"><h2>Earthquakes per day</h2><canvas id="day" height="90"></canvas></div>
 <div class="row">
  <div class="card"><h2>Events by magnitude band</h2><canvas id="band"></canvas></div>
  <div class="card"><h2>Top 10 regions (event count)</h2><canvas id="reg"></canvas></div>
 </div>
 <div class="row">
  <div class="card"><h2>Region summary</h2><table id="regt"></table></div>
  <div class="card"><h2>Ten strongest events</h2><table id="big"></table></div>
 </div>
</main>
<script>
const D = __DATA__;
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('sub').textContent =
  `USGS data, last ${D.window_days} days, magnitude >= ${D.min_magnitude} | generated ${D.generated_at}`;
[[D.total_events,'Total events'],[D.max_magnitude,'Max magnitude'],[D.avg_magnitude,'Avg magnitude'],
 [D.avg_depth_km+' km','Avg depth'],[D.dropped_events,'Records filtered out']].forEach(([v,l])=>{
  document.getElementById('kpis').insertAdjacentHTML('beforeend',`<div class="card kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`)});
new Chart(document.getElementById('day'),{type:'line',data:{labels:D.by_day.map(x=>x.date),datasets:[{label:'Events',data:D.by_day.map(x=>x.count),borderColor:'#1f3a5f',backgroundColor:'rgba(31,58,95,.15)',fill:true,tension:.25}]},options:{plugins:{legend:{display:false}}}});
new Chart(document.getElementById('band'),{type:'bar',data:{labels:D.by_magnitude_band.map(x=>x.band),datasets:[{data:D.by_magnitude_band.map(x=>x.count),backgroundColor:'#e9a23b'}]},options:{plugins:{legend:{display:false}}}});
new Chart(document.getElementById('reg'),{type:'bar',data:{labels:D.top_regions.map(x=>x.region),datasets:[{data:D.top_regions.map(x=>x.count),backgroundColor:'#2a9d8f'}]},options:{indexAxis:'y',plugins:{legend:{display:false}}}});
document.getElementById('regt').innerHTML='<tr><th>Region</th><th>Count</th><th>Avg mag</th><th>Max mag</th></tr>'+D.top_regions.map(r=>`<tr><td>${esc(r.region)}</td><td>${r.count}</td><td>${r.avg_mag}</td><td>${r.max_mag}</td></tr>`).join('');
document.getElementById('big').innerHTML='<tr><th>Time</th><th>Mag</th><th>Place</th></tr>'+D.biggest_events.map(r=>`<tr><td>${esc(r.time)}</td><td>${r.mag}</td><td>${esc(r.place)}</td></tr>`).join('');
</script></body></html>
"""
