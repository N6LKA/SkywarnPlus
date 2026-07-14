#!/usr/bin/python3

"""
Allmon3_Compat.py
===============================================================================
Companion script for SkywarnPlus that writes active NWS weather alerts and
(optionally) current weather conditions to the Allmon3 web root so they can
be displayed via Allmon3's iframepre/iframepost feature.

On ASL3, SkywarnPlus runs as the 'asterisk' user which cannot write to
/usr/share/allmon3/. This script runs as root via a separate cron entry,
following the same pattern as ASL3_Supermon_Workaround.py.

This file is part of SkywarnPlus.
SkywarnPlus is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. SkywarnPlus is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details. You should have received a copy of the GNU General Public License
along with SkywarnPlus. If not, see <https://www.gnu.org/licenses/>.
"""

import os
import json
import logging
import datetime
from collections import OrderedDict

try:
    import requests
except ImportError:
    requests = None

try:
    from ruamel.yaml import YAML
except ImportError:
    YAML = None

SEVERITY_NAMES = {4: "Extreme", 3: "Severe", 2: "Moderate", 1: "Minor", 0: "Unknown"}

BASE_DIR = os.path.dirname(os.path.realpath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.yaml")
COUNTY_CODES_PATH = os.path.join(BASE_DIR, "CountyCodes.md")
DATA_FILE = "/tmp/SkywarnPlus/data.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Static HTML template written to WebRoot on each run.
# The page fetches swp-data.json every 60 s and re-renders without a full reload,
# and attempts to update the parent iframe height after each render.
ALERTS_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="stylesheet" href="css/bootstrap.min.css">
  <style>
    html,body{margin:0;padding:0;height:auto!important;min-height:0!important;background:transparent;font-size:1rem}
    body{padding:4px 8px}
    .swp-wx{padding:4px 8px;margin-bottom:3px;border-radius:3px;background:rgba(255,255,255,.08);text-align:center}
    .swp-wx-title{font-weight:600;margin-bottom:2px}
    .swp-alert{padding:3px 8px;margin-bottom:3px;border-radius:3px;font-weight:500;text-align:center}
    .swp-extreme{background:#7a0000;color:#fff}
    .swp-severe{background:#b83200;color:#fff}
    .swp-moderate{background:#9a5a00;color:#fff}
    .swp-minor{background:#7a6000;color:#fff}
    .swp-unknown{background:#444;color:#fff}
  </style>
</head>
<body>
  <div id="swp"></div>
  <script>
    var SEV={extreme:'swp-extreme',severe:'swp-severe',moderate:'swp-moderate',minor:'swp-minor'};
    function resizeParent(){
      try{
        var h=document.body.scrollHeight,fr=window.parent.document.querySelectorAll('iframe');
        for(var i=0;i<fr.length;i++){
          try{if(fr[i].contentWindow===window){fr[i].style.height=h+'px';break;}}catch(e){}
        }
      }catch(e){}
    }
    function render(d){
      var h='';
      if(d.weather){
        var w=d.weather;
        var title='Weather conditions'+(d.weather_label?': '+d.weather_label:'');
        var wind=w.wind_dir+' '+w.wind_mph+' mph';
        if(w.wind_gust_mph) wind+=' (gust '+w.wind_gust_mph+' mph)';
        var details='Temperature: '+w.temp_f+'&deg;F, '+w.temp_c+'&deg;C'+
          ' &nbsp;|&nbsp; Humidity: '+w.humidity+'%'+
          ' &nbsp;|&nbsp; Wind: '+wind;
        if(w.condition) details+=' &nbsp;|&nbsp; '+w.condition;
        h+='<div class="swp-wx">'+
           '<div class="swp-wx-title">'+title+'</div>'+
           '<div>'+details+'</div>'+
           '</div>';
      }
      (d.alerts||[]).forEach(function(a){
        var c=SEV[(a.severity||'').toLowerCase()]||'swp-unknown';
        h+='<div class="swp-alert '+c+'"><strong>'+a.title+'</strong> ['+a.counties.join(', ')+']</div>';
      });
      document.getElementById('swp').innerHTML=h;
      resizeParent();
    }
    function poll(){
      fetch('swp-data.json?_='+Date.now())
        .then(function(r){return r.json();})
        .then(render)
        .catch(function(){});
    }
    poll();
    setInterval(poll,60000);
  </script>
</body>
</html>
"""


def load_config():
    if YAML is None:
        logging.error("ruamel.yaml not installed")
        return {}
    yaml = YAML()
    with open(CONFIG_FILE, "r") as f:
        return yaml.load(f) or {}


def load_state():
    with open(DATA_FILE, "r") as f:
        state = json.load(f)
    state["last_alerts"] = OrderedDict(
        (x[0], x[1]) for x in state.get("last_alerts", [])
    )
    return state


def load_county_names():
    county_data = {}
    with open(COUNTY_CODES_PATH, "r") as f:
        in_table = False
        for line in f:
            if line.startswith("| County |"):
                in_table = True
                continue
            if not in_table or not line.strip() or line.startswith("##"):
                continue
            parts = [s.strip() for s in line.split("|")[1:-1]]
            if len(parts) == 2:
                county_data[parts[1]] = parts[0]
    return county_data


def degrees_to_cardinal(deg):
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(float(deg) / 22.5) % 16]


def get_weather_wttr(location):
    """Fetch weather from wttr.in (no API key required). Wind gust not available."""
    if not location:
        return None
    try:
        resp = requests.get(
            "https://wttr.in/{}?format=j1".format(location), timeout=10
        )
        if resp.status_code != 200:
            logging.warning("wttr.in returned HTTP %s", resp.status_code)
            return None
        cc = resp.json()["current_condition"][0]
        return {
            "temp_f":        cc.get("temp_F", "?"),
            "temp_c":        cc.get("temp_C", "?"),
            "humidity":      cc.get("humidity", "?"),
            "wind_mph":      cc.get("windspeedMiles", "?"),
            "wind_dir":      cc.get("winddir16Point", "?"),
            "wind_gust_mph": None,
            "condition":     cc["weatherDesc"][0]["value"] if cc.get("weatherDesc") else "",
        }
    except Exception as exc:
        logging.warning("wttr.in fetch failed: %s", exc)
        return None


def get_weather_wunderground(api_key, station):
    """Fetch weather from Weather Underground PWS API. Includes wind gust; no condition string."""
    if not api_key or not station:
        logging.warning("Wunderground requires both WundergroundAPIKey and WundergroundStation")
        return None
    try:
        url = (
            "https://api.weather.com/v2/pws/observations/current"
            "?stationId={}&format=json&units=e&apiKey={}".format(station, api_key)
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            logging.warning("Wunderground API returned HTTP %s", resp.status_code)
            return None
        obs = resp.json()["observations"][0]
        imperial = obs["imperial"]
        temp_f = imperial.get("temp", "?")
        temp_c = round((float(temp_f) - 32) * 5 / 9, 1) if temp_f != "?" else "?"
        gust = imperial.get("windGust")
        return {
            "temp_f":        str(temp_f),
            "temp_c":        str(temp_c),
            "humidity":      str(obs.get("humidity", "?")),
            "wind_mph":      str(imperial.get("windSpeed", "?")),
            "wind_dir":      degrees_to_cardinal(imperial.get("winddir", 0)),
            "wind_gust_mph": str(gust) if gust is not None else None,
            "condition":     "",
        }
    except Exception as exc:
        logging.warning("Wunderground fetch failed: %s", exc)
        return None


def get_weather_tempest(token, station_id):
    """Fetch weather from WeatherFlow Tempest Better Forecast API.
    Includes wind gust and a conditions string (e.g. 'Partly Cloudy').
    If station_id is blank, auto-detects the first station on the account."""
    if not token:
        logging.warning("Tempest requires TempestToken")
        return None
    try:
        if not station_id:
            resp = requests.get(
                "https://swd.weatherflow.com/swd/rest/stations?token={}".format(token),
                timeout=10,
            )
            if resp.status_code != 200:
                logging.warning("Tempest stations API returned HTTP %s", resp.status_code)
                return None
            stations = resp.json().get("stations", [])
            if not stations:
                logging.warning("Tempest: no stations found for this token")
                return None
            station_id = stations[0]["station_id"]
            logging.info("Tempest: auto-detected station ID %s", station_id)

        resp = requests.get(
            "https://swd.weatherflow.com/swd/rest/better_forecast"
            "?station_id={}&token={}".format(station_id, token),
            timeout=10,
        )
        if resp.status_code != 200:
            logging.warning("Tempest forecast API returned HTTP %s", resp.status_code)
            return None
        cc = resp.json().get("current_conditions", {})
        temp_c = cc.get("air_temperature", "?")
        temp_f = round(float(temp_c) * 9 / 5 + 32, 1) if temp_c != "?" else "?"
        wind_ms = cc.get("wind_avg", 0)
        wind_mph = round(float(wind_ms) * 2.23694, 1) if wind_ms is not None else "?"
        gust_ms = cc.get("wind_gust")
        gust_mph = round(float(gust_ms) * 2.23694, 1) if gust_ms is not None else None
        wind_card = cc.get("wind_direction_cardinal") or degrees_to_cardinal(cc.get("wind_direction", 0))
        return {
            "temp_f":        str(temp_f),
            "temp_c":        str(temp_c),
            "humidity":      str(cc.get("relative_humidity", "?")),
            "wind_mph":      str(wind_mph),
            "wind_dir":      wind_card,
            "wind_gust_mph": str(gust_mph) if gust_mph is not None else None,
            "condition":     cc.get("conditions", ""),
        }
    except Exception as exc:
        logging.warning("Tempest fetch failed: %s", exc)
        return None


def main():
    if os.geteuid() != 0:
        logging.error("Must run as root")
        return

    if not os.path.isfile(DATA_FILE):
        logging.warning("SWP data file not found, skipping")
        return

    config = load_config()
    cfg = config.get("Allmon3", {})

    if not cfg.get("Enable", False):
        return

    web_root         = cfg.get("WebRoot", "/usr/share/allmon3")
    weather_on       = cfg.get("WeatherEnable", False)
    weather_loc      = cfg.get("WeatherLocation", "")
    weather_label    = cfg.get("WeatherLabel", "")
    weather_provider = cfg.get("WeatherProvider", "wttr").lower()
    wu_api_key       = cfg.get("WundergroundAPIKey", "")
    wu_station       = cfg.get("WundergroundStation", "")
    tempest_token    = cfg.get("TempestToken", "")
    tempest_station  = cfg.get("TempestStationID", "")

    state       = load_state()
    county_data = load_county_names()

    alerts = []
    for title, entries in state["last_alerts"].items():
        counties = sorted(
            set(county_data.get(e["county_code"], e["county_code"]) for e in entries)
        )
        severity_raw = entries[0].get("severity", 0) if entries else 0
        severity = SEVERITY_NAMES.get(severity_raw, "Unknown") if isinstance(severity_raw, int) else str(severity_raw)
        end_time = entries[0].get("end_time_utc", "") if entries else ""
        alerts.append({
            "title":    title,
            "severity": severity,
            "counties": counties,
            "end_time": end_time,
        })

    weather = None
    if weather_on:
        if requests is None:
            logging.error("requests library not available")
        elif weather_provider == "wunderground":
            weather = get_weather_wunderground(wu_api_key, wu_station)
            if weather is None:
                logging.warning("Wunderground failed, falling back to wttr.in")
                weather = get_weather_wttr(weather_loc)
        elif weather_provider == "tempest":
            weather = get_weather_tempest(tempest_token, tempest_station)
            if weather is None:
                logging.warning("Tempest failed, falling back to wttr.in")
                weather = get_weather_wttr(weather_loc)
        else:
            weather = get_weather_wttr(weather_loc)

    os.makedirs(web_root, exist_ok=True)

    payload = {
        "alerts":    alerts,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if weather:
        payload["weather"]       = weather
        payload["weather_label"] = weather_label

    json_path = os.path.join(web_root, "swp-data.json")
    with open(json_path, "w") as f:
        json.dump(payload, f)

    html_path = os.path.join(web_root, "swp-alerts.html")
    with open(html_path, "w") as f:
        f.write(ALERTS_HTML)

    logging.info("Wrote %s and %s (%d alert(s))", json_path, html_path, len(alerts))


if __name__ == "__main__":
    main()

