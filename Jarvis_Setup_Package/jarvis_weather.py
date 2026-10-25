"""
jarvis_weather.py - Real weather, no API key.

Uses Open-Meteo (free, keyless, non-commercial friendly) for the forecast and
their geocoding endpoint to turn a city name into coordinates.

Location resolution order:
  1. ~/.openjarvis/location      -> "Bengaluru" or "12.97,77.59"
  2. IP geolocation (ip-api.com, keyless)
"""

import json
import os
import urllib.parse
import urllib.request

import jarvis_state

LOCATION_FILE = os.path.expanduser("~/.openjarvis/location")
UA = "JarvisPersonalAssistant/2.0"

# WMO weather interpretation codes -> plain English
_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def describe_code(code):
    try:
        return _CODES.get(int(code), "unknown conditions")
    except (TypeError, ValueError):
        return "unknown conditions"


def _coords():
    """-> (lat, lon, place_name)"""
    # 1. explicit location file
    try:
        if os.path.exists(LOCATION_FILE):
            raw = open(LOCATION_FILE, encoding="utf-8").read().strip()
            if raw:
                if "," in raw and all(p.strip().replace(".", "-", 1).replace("-", "", 1).isdigit()
                                      for p in raw.split(",")[:2]):
                    lat, lon = [float(p) for p in raw.split(",")[:2]]
                    return lat, lon, raw
                data = _get("https://geocoding-api.open-meteo.com/v1/search?"
                            + urllib.parse.urlencode({"name": raw, "count": 1, "language": "en", "format": "json"}))
                res = (data.get("results") or [None])[0]
                if res:
                    return float(res["latitude"]), float(res["longitude"]), res.get("name", raw)
    except Exception:
        pass

    # 2. IP geolocation
    try:
        data = _get("http://ip-api.com/json/?fields=city,lat,lon,status", timeout=8)
        if data.get("status") == "success" and data.get("city"):
            return float(data["lat"]), float(data["lon"]), data["city"]
    except Exception:
        pass
    return None, None, None


def current(force=False):
    """Full weather payload for the HUD and spoken briefings."""
    if not force:
        cached, _ = jarvis_state.get_cache("weather", 900)
        if cached:
            return cached

    lat, lon, place = _coords()
    if lat is None:
        payload = {"ok": False, "error": "location-unavailable"}
        jarvis_state.put_cache("weather", payload)
        return payload

    url = ("https://api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode({
               "latitude": lat, "longitude": lon,
               "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                           "weather_code,wind_speed_10m,is_day",
               "daily": "weather_code,temperature_2m_max,temperature_2m_min",
               "timezone": "auto", "forecast_days": 4,
           }))
    try:
        data = _get(url)
    except Exception as exc:
        payload = {"ok": False, "error": f"forecast-failed: {exc}"}
        jarvis_state.put_cache("weather", payload)
        return payload

    cur = data.get("current", {})
    daily = data.get("daily", {})
    days = []
    for i, date in enumerate(daily.get("time", [])):
        days.append({
            "date": date,
            "code": (daily.get("weather_code") or [None])[i],
            "text": describe_code((daily.get("weather_code") or [None])[i]),
            "max": (daily.get("temperature_2m_max") or [None])[i],
            "min": (daily.get("temperature_2m_min") or [None])[i],
        })

    payload = {
        "ok": True,
        "place": place,
        "temp": cur.get("temperature_2m"),
        "feels": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "code": cur.get("weather_code"),
        "text": describe_code(cur.get("weather_code")),
        "is_day": bool(cur.get("is_day", 1)),
        "days": days,
        "updated": data.get("current", {}).get("time"),
    }
    jarvis_state.put_cache("weather", payload)
    return payload


def briefing():
    """One spoken sentence + a short outlook."""
    w = current()
    if not w.get("ok"):
        return None
    head = (f"Weather in {w['place']}: {w['temp']} degrees, {w['text']}, "
            f"feels like {w['feels']} degrees. Humidity {w['humidity']}%, wind {w['wind']} km/h.")
    outlook = ""
    if len(w["days"]) > 1:
        d = w["days"][1]
        outlook = f" Tomorrow: {d['text']}, high of {d['max']} and low of {d['min']} degrees."
    return head + outlook
