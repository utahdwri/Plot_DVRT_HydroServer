import argparse
import csv
from datetime import datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from urllib.parse import parse_qs, urlencode, urlparse

import requests
import plotly.graph_objects as go

# --- Configuration Constants ---
BASE_URL = "https://hydroserver.waterrights.utah.gov/api/data"
HYDROSERVER_VISUALIZE_URL = "https://hydroserver.waterrights.utah.gov/visualize-data"
DEFAULT_START_DATE = None
DEFAULT_END_DATE = None
PAGE_SIZE = 10000
DVRT_DAILY_CHART_URL = "https://waterrights.utah.gov/dvrtdb/daily-chart.asp"
LOCAL_SERVER_HOST = "127.0.0.1"
LOCAL_SERVER_PORT = 55620


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
        raise ValueError(
            f"{label} must be an ISO date or datetime, got {value!r}"
        ) from error


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
    values = parse_qs(query_text, keep_blank_values=False)

    return {
        "station_id": first_present_value(
            values.get("station_id", [None])[0],
            values.get("STATION_ID", [None])[0],
        ),
        "start_date": first_present_value(
            values.get("start_date", [None])[0],
            values.get("start", [None])[0],
            values.get("begin_date", [None])[0],
        ),
        "end_date": first_present_value(
            values.get("end_date", [None])[0],
            values.get("end", [None])[0],
            values.get("stop_date", [None])[0],
        ),
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot DVRT and HydroServer daily observations."
    )
    parser.add_argument(
        "url",
        nargs="?",
        help=(
            "Optional URL or query string, for example "
            "'?station_id=4884&start_date=2020-01-01&end_date=2026-06-30'."
        ),
    )
    parser.add_argument("--station-id", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start a local HTTP server that accepts station_id/start_date/end_date in the URL.",
    )
    parser.add_argument("--host", default=LOCAL_SERVER_HOST)
    parser.add_argument("--port", type=int, default=LOCAL_SERVER_PORT)
    return parser.parse_args()


def resolve_options(url_or_query=None, station_id=None, start_date=None, end_date=None):
    url_options = parse_url_options(url_or_query)
    station_id_value = first_present_value(
        station_id,
        url_options.get("station_id"),
    )
    if not station_id_value:
        raise ValueError("Missing required URL parameter: station_id")

    resolved_station_id = int(station_id_value)
    resolved_start_date = first_present_value(
        start_date,
        url_options.get("start_date"),
        DEFAULT_START_DATE,
    )
    resolved_end_date = first_present_value(
        end_date,
        url_options.get("end_date"),
        DEFAULT_END_DATE,
    )
    return resolved_station_id, resolved_start_date, resolved_end_date


def build_plot_query(station_id, start_date=None, end_date=None):
    plot_url_params = {"station_id": station_id}
    if start_date:
        plot_url_params["start_date"] = start_date
    if end_date:
        plot_url_params["end_date"] = end_date
    return urlencode(plot_url_params)


def print_plot_url(station_id, start_date=None, end_date=None, base_url=None):
    query = build_plot_query(station_id, start_date, end_date)
    if base_url:
        print(f"Plot URL: {base_url}?{query}", flush=True)
    else:
        print(f"Plot URL: ?{query}", flush=True)


def build_hydroserver_visualize_url(thing_id, datastream_id):
    if not thing_id or not datastream_id:
        return None
    return (
        f"{HYDROSERVER_VISUALIZE_URL}?"
        f"{urlencode({'sites': thing_id, 'datastreams': datastream_id})}"
    )


def build_dvrt_plot_url(station_id):
    return f"{DVRT_DAILY_CHART_URL}?{urlencode({'STATION_ID': station_id})}"


def build_csv_download_url(base_url, station_id, start_date=None, end_date=None):
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/download.csv?{build_plot_query(station_id, start_date, end_date)}"


def build_diff_download_url(base_url, station_id, start_date=None, end_date=None):
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/diff.txt?{build_plot_query(station_id, start_date, end_date)}"


def get_nested_value(data, path):
    value = data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def get_datastream_thing_id(datastream):
    return first_present_value(
        datastream.get("thing_id"),
        datastream.get("thingId"),
        datastream.get("thing"),
        datastream.get("thingUid"),
        get_nested_value(datastream, ["thing", "id"]),
        get_nested_value(datastream, ["thing", "uid"]),
    )


def parse_hydroserver_datetime(value):
    text = first_present_value(value, "0001-01-01T00:00:00Z")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def get_daily_datastreams(station_id):
    url = f"{BASE_URL}/datastreams"
    datastreams = []
    page = 1

    while True:
        res = requests.get(
            url,
            params={
                "page": page,
                "page_size": PAGE_SIZE,
                "tag": f"STATION_ID:{station_id}",
            },
            timeout=30,
        ).json()
        results = res if isinstance(res, list) else res.get("results", [])

        for stream in results:
            if str(stream.get("intendedTimeSpacingUnit", "")).strip().lower() == "days":
                datastreams.append(stream)

        if isinstance(res, list):
            if len(results) < PAGE_SIZE:
                break
        elif not res.get("next") and len(results) < PAGE_SIZE:
            break
        page += 1

    return datastreams


def get_hydroserver_observations(datastream_id, start_datetime=None, end_datetime=None):
    obs_url = f"{BASE_URL}/datastreams/{datastream_id}/observations"
    observations = []
    page = 1

    while True:
        obs_params = {"page": page, "page_size": PAGE_SIZE}
        if start_datetime is not None:
            obs_params["phenomenon_time_min"] = iso_z(start_datetime)
        if end_datetime is not None:
            obs_params["phenomenon_time_max"] = iso_z(end_datetime)

        res = requests.get(obs_url, params=obs_params, timeout=30).json()
        results = res if isinstance(res, list) else res.get("results", [])
        observations.extend(results)

        if isinstance(res, list):
            if len(results) < PAGE_SIZE:
                break
        elif not res.get("next") and len(results) < PAGE_SIZE:
            break
        page += 1

    return observations


def get_dvrt_rows(station_id, start_date=None, end_date=None, start_datetime=None, end_datetime=None):
    dvrt_params = {"station_id": station_id, "f": "json"}
    if start_date:
        dvrt_params["start_date"] = start_date
    if end_date:
        dvrt_params["end_date"] = end_date
    dvrt_url = f"{DVRT_DAILY_CHART_URL}?{urlencode(dvrt_params)}"
    print(f"Querying DVRT daily database via: {dvrt_url}")

    dvrt_response = requests.get(dvrt_url, timeout=30)
    dvrt_response.raise_for_status()
    dvrt_json = json.loads(dvrt_response.text.replace("\t", ""))

    rows = []
    for row in dvrt_json.get("data", []):
        if "date" not in row:
            continue
        row_date = datetime.fromisoformat(row["date"])
        if start_datetime is not None and row_date < start_datetime.replace(tzinfo=None):
            continue
        if end_datetime is not None and row_date > end_datetime.replace(tzinfo=None):
            continue
        rows.append(row)

    return dvrt_json, rows


def calculate_diff_counts(station_id, start_date=None, end_date=None):
    """Calculates diff metrics efficiently to expose onto the plot summary string."""
    start_datetime = start_of_day(start_date)
    end_datetime = end_of_day(end_date)
    datastreams = get_daily_datastreams(station_id)

    hs_by_date = {}
    if datastreams:
        latest_stream = max(
            datastreams,
            key=lambda x: parse_hydroserver_datetime(x.get("phenomenonEndTime")),
        )
        observations = get_hydroserver_observations(
            latest_stream["id"],
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        for obs in observations:
            if "phenomenonTime" in obs and "result" in obs:
                dt_str = datetime.fromisoformat(obs["phenomenonTime"].replace("Z", "+00:00")).date().isoformat()
                hs_by_date[dt_str] = round(float(obs["result"]), 6)

    _, dvrt_rows = get_dvrt_rows(
        station_id,
        start_date=start_date,
        end_date=end_date,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
    )
    dvrt_by_date = {}
    for row in dvrt_rows:
        if "date" in row and "value" in row:
            dt_str = datetime.fromisoformat(row["date"]).date().isoformat()
            dvrt_by_date[dt_str] = round(float(row["value"]), 6)

    all_dates = set(dvrt_by_date.keys()).union(set(hs_by_date.keys()))

    mismatches = 0
    missing_hs = 0
    missing_dvrt = 0

    for d in all_dates:
        v_dvrt = dvrt_by_date.get(d)
        v_hs = hs_by_date.get(d)

        if v_dvrt is not None and v_hs is not None:
            if v_dvrt != v_hs:
                mismatches += 1
        elif v_dvrt is not None:
            missing_hs += 1
        elif v_hs is not None:
            missing_dvrt += 1

    return mismatches, missing_hs, missing_dvrt


def build_diff_report(station_id, start_date=None, end_date=None):
    start_datetime = start_of_day(start_date)
    end_datetime = end_of_day(end_date)
    datastreams = get_daily_datastreams(station_id)

    hs_by_date = {}
    if datastreams:
        latest_stream = max(
            datastreams,
            key=lambda x: parse_hydroserver_datetime(x.get("phenomenonEndTime")),
        )
        observations = get_hydroserver_observations(
            latest_stream["id"],
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        for obs in observations:
            if "phenomenonTime" in obs and "result" in obs:
                dt_str = datetime.fromisoformat(obs["phenomenonTime"].replace("Z", "+00:00")).date().isoformat()
                hs_by_date[dt_str] = round(float(obs["result"]), 6)

    _, dvrt_rows = get_dvrt_rows(
        station_id,
        start_date=start_date,
        end_date=end_date,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
    )
    dvrt_by_date = {}
    for row in dvrt_rows:
        if "date" in row and "value" in row:
            dt_str = datetime.fromisoformat(row["date"]).date().isoformat()
            dvrt_by_date[dt_str] = round(float(row["value"]), 6)

    all_dates = sorted(list(set(dvrt_by_date.keys()).union(set(hs_by_date.keys()))))

    output = io.StringIO()
    output.write(f"=== TIME SERIES DATA DIFF REPORT ===\n")
    output.write(f"Station ID: {station_id}\n")
    output.write(f"Generated On: {datetime.now().isoformat()}\n")
    output.write(f"Total Combined Dates: {len(all_dates)}\n")
    output.write(f"------------------------------------\n\n")

    mismatches = []
    missing_in_hs = []
    missing_in_dvrt = []

    for d in all_dates:
        v_dvrt = dvrt_by_date.get(d)
        v_hs = hs_by_date.get(d)

        if v_dvrt is not None and v_hs is not None:
            if v_dvrt != v_hs:
                mismatches.append((d, v_dvrt, v_hs))
        elif v_dvrt is not None:
            missing_in_hs.append((d, v_dvrt))
        elif v_hs is not None:
            missing_in_dvrt.append((d, v_hs))

    output.write(f"SUMMARY OF FINDINGS:\n")
    output.write(f" -> Value Mismatches: {len(mismatches)}\n")
    output.write(f" -> Missing in HydroServer: {len(missing_in_hs)}\n")
    output.write(f" -> Missing in DVRT: {len(missing_in_dvrt)}\n\n")
    output.write(f"------------------------------------\n\n")

    if mismatches:
        output.write(f"VALUE MISMATCH DETAILS:\n")
        output.write(f"{'Date':<12} | {'DVRT Value':<15} | {'HydroServer Value':<15} | {'Difference':<15}\n")
        output.write(f"-" * 65 + "\n")
        for d, vd, vh in mismatches:
            diff = round(vd - vh, 6)
            output.write(f"{d:<12} | {vd:<15} | {vh:<15} | {diff:<15}\n")
        output.write("\n")

    if missing_in_hs:
        output.write(f"MISSING IN HYDROSERVER (Present in DVRT):\n")
        output.write(f"{'Date':<12} | {'DVRT Value':<15}\n")
        output.write(f"-" * 32 + "\n")
        for d, vd in missing_in_hs:
            output.write(f"{d:<12} | {vd:<15}\n")
        output.write("\n")

    if missing_in_dvrt:
        output.write(f"MISSING IN DVRT (Present in HydroServer):\n")
        output.write(f"{'Date':<12} | {'HydroServer Value':<15}\n")
        output.write(f"-" * 32 + "\n")
        for d, vh in missing_in_dvrt:
            output.write(f"{d:<12} | {vh:<15}\n")
        output.write("\n")

    if not mismatches and not missing_in_hs and not missing_in_dvrt:
        output.write("SUCCESS: Datasets match perfectly. No differences found.\n")

    return output.getvalue()


def build_csv_data(station_id, start_date=None, end_date=None):
    start_datetime = start_of_day(start_date)
    end_datetime = end_of_day(end_date)
    datastreams = get_daily_datastreams(station_id)
    hydroserver_rows = []

    if datastreams:
        latest_stream = max(
            datastreams,
            key=lambda x: parse_hydroserver_datetime(x.get("phenomenonEndTime")),
        )
        observations = get_hydroserver_observations(
            latest_stream["id"],
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        for obs in observations:
            if "phenomenonTime" not in obs or "result" not in obs:
                continue
            hydroserver_rows.append(
                {
                    "station_id": station_id,
                    "date": datetime.fromisoformat(
                        obs["phenomenonTime"].replace("Z", "+00:00")
                    ).date().isoformat(),
                    "HydroServer": obs["result"],
                }
            )

    _, dvrt_rows = get_dvrt_rows(
        station_id,
        start_date=start_date,
        end_date=end_date,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
    )
    rows_by_date = {}
    for row in dvrt_rows:
        if "date" not in row or "value" not in row:
            continue
        row_date = datetime.fromisoformat(row["date"]).date().isoformat()
        rows_by_date.setdefault(
            row_date,
            {"station_id": station_id, "date": row_date, "DVRT": "", "HydroServer": ""},
        )["DVRT"] = row["value"]

    for row in hydroserver_rows:
        row_date = row["date"]
        rows_by_date.setdefault(
            row_date,
            {"station_id": station_id, "date": row_date, "DVRT": "", "HydroServer": ""},
        )["HydroServer"] = row["HydroServer"]

    rows = [
        rows_by_date[row_date]
        for row_date in sorted(rows_by_date)
    ]

    output = io.StringIO()
    fieldnames = [
        "station_id",
        "date",
        "DVRT",
        "HydroServer",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def build_figure(station_id, start_date=None, end_date=None, base_url=None):
    start_datetime = start_of_day(start_date)
    end_datetime = end_of_day(end_date)
    print_plot_url(station_id, start_date, end_date, base_url=base_url)

    datastreams = get_daily_datastreams(station_id)

    if not datastreams:
        print(
            "Warning: No HydroServer datastream with spacing_unit == 'days' "
            f"found for Station ID {station_id}"
        )
        hs_dates, hs_values, hs_observations = [], [], []
        hydroserver_url = None
    else:
        latest_stream = max(
            datastreams,
            key=lambda x: parse_hydroserver_datetime(x.get("phenomenonEndTime")),
        )
        print(
            f"Using Daily Datastream: {latest_stream['id']} "
            f"({latest_stream.get('name', 'unnamed')})"
        )
        hydroserver_url = build_hydroserver_visualize_url(
            get_datastream_thing_id(latest_stream),
            latest_stream["id"],
        )

        hs_observations = get_hydroserver_observations(
            latest_stream["id"],
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        hs_points = [
            (
                datetime.fromisoformat(
                    obs["phenomenonTime"].replace("Z", "+00:00")
                ).date().isoformat(),
                float(obs["result"]),
            )
            for obs in hs_observations
            if "phenomenonTime" in obs and "result" in obs
        ]
        hs_dates = [date for date, _ in hs_points]
        hs_values = [value for _, value in hs_points]

    dvrt_json, dvrt_rows = get_dvrt_rows(
        station_id,
        start_date=start_date,
        end_date=end_date,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
    )
    dvrt_dates = [
        datetime.fromisoformat(row["date"]) for row in dvrt_rows if "date" in row
    ]
    dvrt_values = [float(row["value"]) for row in dvrt_rows if "value" in row]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dvrt_dates,
            y=dvrt_values,
            mode="lines+markers",
            name=f"DVRT Daily ({len(dvrt_rows)} pts)",
            line=dict(color="firebrick", width=2),
            marker=dict(color="firebrick", size=20),
        )
    )

    if hs_observations:
        fig.add_trace(
            go.Scatter(
                x=hs_dates,
                y=hs_values,
                mode="lines+markers",
                name=f"HydroServer ({len(hs_observations)} pts)",
                line=dict(color="royalblue", width=1.5, dash="dash"),
                marker=dict(color="royalblue", size=12),
            )
        )

    station_name = dvrt_json.get("station_name", f"Station {station_id}")
    y_units = dvrt_json.get("units", "Value")

    annotations = []
    dvrt_url = build_dvrt_plot_url(station_id)
    csv_url = build_csv_download_url(base_url, station_id, start_date, end_date)
    diff_url = build_diff_download_url(base_url, station_id, start_date, end_date)

    button_y = 0.99
    current_x = 0.01

    if hydroserver_url:
        annotations.append(
            dict(
                text=f"<a href='{hydroserver_url}' target='_blank'>HydroServer page</a>",
                xref="paper",
                yref="paper",
                x=current_x,
                y=button_y,
                showarrow=False,
                xanchor="left",
                yanchor="top",
                font=dict(size=26, color="royalblue"),
                bgcolor="rgba(255,255,255,0.92)",
                bordercolor="royalblue",
                borderwidth=1,
                borderpad=4,
            )
        )
        current_x += 0.22

    annotations.append(
        dict(
            text=f"<a href='{dvrt_url}' target='_blank'>DVRT page</a>",
            xref="paper",
            yref="paper",
            x=current_x,
            y=button_y,
            showarrow=False,
            xanchor="left",
            yanchor="top",
            font=dict(size=26, color="firebrick"),
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="firebrick",
            borderwidth=1,
            borderpad=4,
        )
    )
    current_x += 0.16

    if csv_url:
        annotations.append(
            dict(
                text=f"<a href='{csv_url}' target='_blank'>Download CSV</a>",
                xref="paper",
                yref="paper",
                x=current_x,
                y=button_y,
                showarrow=False,
                xanchor="left",
                yanchor="top",
                font=dict(size=26, color="black"),
                bgcolor="rgba(255,255,255,0.92)",
                bordercolor="black",
                borderwidth=1,
                borderpad=4,
            )
        )
        current_x += 0.19

    if diff_url:
        annotations.append(
            dict(
                text=f"<a href='{diff_url}' target='_blank'>Diff</a>",
                xref="paper",
                yref="paper",
                x=current_x,
                y=button_y,
                showarrow=False,
                xanchor="left",
                yanchor="top",
                font=dict(size=26, color="darkorange"),
                bgcolor="rgba(255,255,255,0.92)",
                bordercolor="darkorange",
                borderwidth=1,
                borderpad=4,
            )
        )

    # --- Fetch Diff Metrics & Build Summary Text Box on Plot ---
    try:
        mismatches, missing_hs, missing_dvrt = calculate_diff_counts(station_id, start_date, end_date)
        summary_html = (
            "<b>Summary of Findings:</b><br>"
            f"• Value Mismatches: {mismatches}<br>"
            f"• Missing in HydroServer: {missing_hs}<br>"
            f"• Missing in DVRT: {missing_dvrt}"
        )
        annotations.append(
            dict(
                text=summary_html,
                xref="paper",
                yref="paper",
                x=0.01,
                y=0.91,  # Positioned safely underneath the button row
                showarrow=False,
                xanchor="left",
                yanchor="top",
                font=dict(size=20, color="#333333"),
                bgcolor="rgba(255,255,255,0.95)",
                bordercolor="#cccccc",
                borderwidth=1,
                borderpad=8,
            )
        )
    except Exception as e:
        print(f"Could not print Summary of Findings on plot: {e}")

    fig.update_layout(
        title=dict(
            text=f"Daily Time Series Comparison ({station_id}): {station_name}",
            font=dict(size=34),
        ),
        xaxis_title="Date",
        yaxis_title=y_units,
        hovermode="x unified",
        template="plotly_white",
        font=dict(size=24),
        xaxis=dict(title=dict(font=dict(size=28)), tickfont=dict(size=22)),
        yaxis=dict(title=dict(font=dict(size=28)), tickfont=dict(size=22)),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=24),
        ),
        annotations=annotations,
        hoverlabel=dict(font=dict(size=24)),
        margin=dict(t=125),
    )
    return fig


class PlotRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(404)
            self.end_headers()
            return

        try:
            station_id, start_date, end_date = resolve_options(self.path)
            parsed_path = urlparse(self.path).path

            if parsed_path == "/download.csv":
                csv_text = build_csv_data(station_id, start_date, end_date)
                body = csv_text.encode("utf-8")
                filename = f"station_{station_id}_daily_timeseries.csv"
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed_path == "/diff.txt":
                diff_text = build_diff_report(station_id, start_date, end_date)
                body = diff_text.encode("utf-8")
                filename = f"station_{station_id}_timeseries_diff.txt"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

           base_url = f"http://{self.headers.get('Host', f'{LOCAL_SERVER_HOST}:{LOCAL_SERVER_PORT}')}/"
            fig = build_figure(station_id, start_date, end_date, base_url=base_url)
            body = fig.to_html(include_plotlyjs=True, full_html=True).encode("utf-8")
        except Exception as error:
            self.send_text_response(500, f"Failed to build response: {error}")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text_response(self, status_code, message):
        body = message.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve_plots(host=LOCAL_SERVER_HOST, port=LOCAL_SERVER_PORT):
    gcp_port = int(os.environ.get("PORT", port))
    gcp_host = "0.0.0.0" if os.environ.get("PORT") else host

    server = ThreadingHTTPServer((gcp_host, gcp_port), PlotRequestHandler)
    first_example = build_plot_query(
        9864,
        start_date="2026-06-01",
        end_date="2026-06-30",
    )
    second_example = build_plot_query(
        9634,
        start_date="2026-06-01",
        end_date="2026-06-30",
    )
    print(f"Serving plots on http://{gcp_host}:{gcp_port}. Include station_id in the URL:", flush=True)
    print(f"http://{gcp_host}:{gcp_port}/?{first_example}", flush=True)
    print(f"http://{gcp_host}:{gcp_port}/?{second_example}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    args = parse_args()
    has_direct_station_request = any(
        [
            args.url,
            args.station_id,
        ]
    )

    if os.environ.get("PORT") or args.serve or not has_direct_station_request:
        serve_plots(host=args.host, port=args.port)
    else:
        station_id, start_date, end_date = resolve_options(
            args.url,
            station_id=args.station_id,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        base_url = f"http://{args.host}:{args.port}/"
        figure = build_figure(station_id, start_date, end_date, base_url=base_url)
        figure.show()
