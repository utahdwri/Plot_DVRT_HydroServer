import argparse
import csv
from datetime import datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlparse
import time as time_module

import requests
import pandas as pd
import plotly.graph_objects as go
from hydroserverpy import HydroServer

# --- Configuration Constants ---
BASE_URL = "https://hydroserver.waterrights.utah.gov/api/data"
HYDROSERVER_VISUALIZE_URL_BASE = "https://hydroserver.waterrights.utah.gov/visualize-data"
DEFAULT_START_DATE = None
DEFAULT_END_DATE = None
PAGE_SIZE = 10000
DVRT_DAILY_CHART_URL = "https://waterrights.utah.gov/dvrtdb/daily-chart.asp"
DVRT_REALTIME_CHART_URL = "https://www.waterrights.utah.gov/dvrtdb/realtime-chart.asp"
LOCAL_SERVER_HOST = "0.0.0.0"
LOCAL_SERVER_PORT = 55620

# --- Compare Configs ---
HS_HOST = "https://hydroserver.waterrights.utah.gov/"
WORKSPACE_NAME = "Prod Measurement Data - Final"
WORKSPACE_ID = "019eb774-8b9d-74fe-b6be-b33ab48cfabe"
TARGET_COLLECTION_SYS_NAME = ["BEAR_UPL"]
OBSERVATION_PAGE_SIZE = 15000


# --- UTILITY CODES (Plot & Clean up) ---
def first_present_value(*values):
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def parse_date(value, label):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO date or datetime, got {value!r}") from error


def start_of_day(value):
    parsed = parse_date(value, "start_date")
    if parsed is None or parsed.time() != time.min:
        return parsed
    return datetime.combine(parsed.date(), time.min, tzinfo=parsed.tzinfo)


def end_of_day(value):
    parsed = parse_date(value, "end_date")
    if parsed is None or parsed.time() != time.min:
        return parsed
    return datetime.combine(parsed.date(), time.max, tzinfo=parsed.tzinfo)


def iso_z(value):
    if value is None:
        return None
    text = value.isoformat()
    return text.replace("+00:00", "Z")


def parse_url_options(url_or_query):
    if not url_or_query:
        return {}
    parsed = urlparse(url_or_query)
    query_text = parsed.query or url_or_query.lstrip("?")
    if "/diff" in query_text:
        query_text = query_text.replace("/diff", "")
    values = parse_qs(query_text, keep_blank_values=False)
    station_raw = first_present_value(values.get("station_id", [None])[0], values.get("STATION_ID", [None])[0])
    if station_raw and "/diff" in station_raw:
        station_raw = station_raw.split("/diff")[0]
    return {
        "station_id": station_raw,
        "start_date": first_present_value(values.get("start_date", [None])[0], values.get("start", [None])[0]),
        "end_date": first_present_value(values.get("end_date", [None])[0], values.get("end", [None])[0]),
    }


def resolve_options(url_or_query=None, station_id=None, start_date=None, end_date=None):
    url_options = parse_url_options(url_or_query)
    station_id_value = first_present_value(station_id, url_options.get("station_id"))
    if not station_id_value:
        return None, None, None
    return int(station_id_value), url_options.get("start_date"), url_options.get("end_date")


def build_plot_query(station_id, start_date=None, end_date=None):
    params = {"station_id": station_id}
    if start_date: params["start_date"] = start_date
    if end_date: params["end_date"] = end_date
    return urlencode(params)


# --- COMPARE SCRIPT DATA PROCESSING CONVERSIONS ---
def normalize_spacing_unit(spacing_unit):
    return "" if pd.isna(spacing_unit) else str(spacing_unit).strip().lower()


def get_dvrt_url(station_id, spacing_unit):
    station_id = str(station_id).split(".")[0]
    spacing = normalize_spacing_unit(spacing_unit)
    if spacing == "days":
        return f"{DVRT_DAILY_CHART_URL}?station_id={station_id}&f=json"
    if spacing in ["hours", "minutes"]:
        return f"{DVRT_REALTIME_CHART_URL}?station_id={station_id}&f=json-all"
    raise ValueError(f"Unsupported spacing unit: {spacing_unit}")


def make_timeseries_dataframe(data, spacing_unit, source):
    rows = data.get("data", data.get("values", data.get("results", data))) if isinstance(data, dict) else data
    df = pd.DataFrame(rows)
    if df.empty: return df
    
    date_col = next((c for c in df.columns if str(c).lower() in ["date", "datetime", "phenomenontime", "time"]), df.columns[0])
    val_col = next((c for c in df.columns if str(c).lower() in ["value", "result", "resultvalue"]), df.columns[1] if len(df.columns) > 1 else df.columns[0])
    
    df = df[[date_col, val_col]].copy()
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce").round(6)
    return df.dropna().reset_index(drop=True)


# --- CORE EXECUTION: RUN LIVE WEB STATIONS RUNTIME COMPARE ---
def execute_web_comparison():
    hs_api = HydroServer(host=HS_HOST)
    
    # Simple fallbacks to direct requests instead of internal nested dataframe tools for clean API deployment
    res_t = requests.get(f"{BASE_URL}/things?workspace_id={WORKSPACE_ID}&page_size=100", timeout=30).json()
    things = res_t.get("results", [])
    
    output = io.StringIO()
    output.write(f"=== HYDROSERVER LIVE COMPARISON REPORT ===\n")
    output.write(f"Workspace ID: {WORKSPACE_ID}\n")
    output.write(f"Generated at: {datetime.now().isoformat()}\n")
    output.write(f"-----------------------------------------\n\n")
    
    for t in things:
        thing_uid = t.get("id")
        thing_name = t.get("name")
        sys_name = t.get("samplingFeatureType", "") # tracking feature
        
        output.write(f"Thing: {thing_name} ({thing_uid}) [{sys_name}]\n")
        
        # Pull associated datastreams
        res_d = requests.get(f"{BASE_URL}/datastreams?thing_id={thing_uid}", timeout=30).json()
        for ds in res_d.get("results", []):
            ds_uid = ds.get("id")
            ds_name = ds.get("name")
            station_id = ds.get("description", "") # Example target mapping anchor
            
            output.write(f"  -> Datastream: {ds_name} | ID: {ds_uid}\n")
    
    return output.getvalue()


# --- PLOT BUILDING METHODS ---
def calculate_diff_counts(station_id, start_date=None, end_date=None):
    return 0, 0, 0, 0


def build_figure(station_id, start_date=None, end_date=None, base_url=None):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=["2026-06-01"], y=[10], mode="lines+markers", name="Sample Data"))
    fig.update_layout(title=f"Station Comparison ({station_id})", template="plotly_white")
    return fig


# --- HTTP ROUTER MANAGEMENT ---
class PlotRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_url = urlparse(self.path)
        parsed_path = parsed_url.path

        # NEW INTERCEPT: Custom routing for the global verification matrix
        if parsed_path == "/compare":
            try:
                report_content = execute_web_comparison()
                body = report_content.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception as e:
                self.send_text_response(500, f"Comparison error: {str(e)}")
                return

        if self.path == "/favicon.ico":
            self.send_response(404)
            self.end_headers()
            return

        try:
            station_id, start_date, end_date = resolve_options(self.path)
            if not station_id:
                self.send_text_response(200, "Welcome! Append ?station_id=XXXX to view system metrics, or visit /compare.")
                return

            base_url = f"http://{self.headers.get('Host', f'{LOCAL_SERVER_HOST}:{LOCAL_SERVER_PORT}')}/"
            fig = build_figure(station_id, start_date, end_date, base_url=base_url)
            body = fig.to_html(include_plotlyjs="cdn", full_html=True).encode("utf-8")
            
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as error:
            self.send_text_response(500, f"Failed to build response: {error}")

    def send_text_response(self, status_code, message):
        body = message.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve_plots():
    port = int(os.environ.get("PORT", LOCAL_SERVER_PORT))
    server = ThreadingHTTPServer(("0.0.0.0", port), PlotRequestHandler)
    print(f"Container listening on port: {port}")
    server.serve_forever()


if __name__ == "__main__":
    serve_plots()
