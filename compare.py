from pathlib import Path
from datetime import datetime
import json
import re
import time

import pandas as pd
import requests
from hydroserverpy import HydroServer

from hydroserver_dataframe_utils import hydro_list_to_flat_df

HS_HOST = "https://hydroserver.waterrights.utah.gov/"
# HS_HOST = "https://hydroservertest.waterrights.utah.gov/"

# WORKSPACE_NAME = "CU_SCADA-final"
WORKSPACE_NAME = "Prod Measurement Data - Final"
WORKSPACE_ID = "019eb774-8b9d-74fe-b6be-b33ab48cfabe"

TARGET_COLLECTION_SYS_NAME = ["BEAR_UPL"]
# TARGET_COLLECTION_SYS_NAME = ["CU_SCADA"]

# TARGET_COLLECTION_SYS_NAME = ["USBR"]

# TARGET_COLLECTION_SYS_NAME = [
# "DUCHESNE",
# "EMERY",
# "STRAWBERRY",
# "SEVIER",
# "UINTAH",
# "CICWCD"]


# TARGET_COLLECTION_SYS_NAME = [
#     "SEVIER_METRIDYNE",
#     "BEAR_UPPER",
#     "EAST_FORK_VIRGIN",
#     "ROCKY_FORD",
# ]

OBSERVATION_PAGE_SIZE = 15000
API_BATCH_SIZE = 10
API_BATCH_WAIT_SECONDS = 0.1

HYDROSERVER_VISUALIZE_URL = f"{HS_HOST.rstrip('/')}/visualize-data"
DVRT_DAILY_CHART_URL = "https://waterrights.utah.gov/dvrtdb/daily-chart.asp"
DVRT_REALTIME_CHART_URL = "https://www.waterrights.utah.gov/dvrtdb/realtime-chart.asp"
LOCAL_COMPARISON_PLOT_URL = "https://plot-dvrt-hydroserver-git-297769208259.us-central1.run.app/"
LOCAL_COMPARISON_START_DATE = "2026-06-15"


def normalize_target_collection_sys_names(target_collection_sys_name):
    if isinstance(target_collection_sys_name, str):
        raw_values = target_collection_sys_name.split(",")
    else:
        raw_values = target_collection_sys_name

    return [
        str(collection_sys_name).strip()
        for collection_sys_name in raw_values
        if str(collection_sys_name).strip()
    ]


def target_collection_sys_names_include_all(target_collection_sys_name):
    return any(
        collection_sys_name.upper() == "ALL"
        for collection_sys_name in normalize_target_collection_sys_names(
            target_collection_sys_name
        )
    )


def workspace_file_slug(workspace_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", workspace_name.lower()).strip("_") or "workspace"


WORKSPACE_FILE_SLUG = workspace_file_slug(WORKSPACE_NAME)
EXPORT_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUT_PATH = Path(__file__).with_name(
    f"{WORKSPACE_FILE_SLUG}_workspace_things_datastreams_{EXPORT_TIMESTAMP}.csv"
)
COMPARE_OUTPUT_PATH = Path(__file__).with_name(
    f"{WORKSPACE_FILE_SLUG}_datastream_comparison_{EXPORT_TIMESTAMP}.csv"
)


# Connect to HydroServer using public read access.
hs_api = HydroServer(host=HS_HOST)


# Get all Things and Datastreams in the workspace.
df_things = hydro_list_to_flat_df(
    hs_api.things.list,
    workspace=WORKSPACE_ID,
)

df_datastreams = hydro_list_to_flat_df(
    hs_api.datastreams.list,
    workspace=WORKSPACE_ID,
)


# Join Datastreams to their parent Things.
df_things = df_things.add_prefix("thing_")
df_datastreams = df_datastreams.add_prefix("datastream_")

things_datastreams_df = pd.merge(
    df_things,
    df_datastreams,
    how="left",
    left_on="thing_uid",
    right_on="datastream_thing_id",
)

target_collection_sys_names = normalize_target_collection_sys_names(
    TARGET_COLLECTION_SYS_NAME
)
if target_collection_sys_names_include_all(TARGET_COLLECTION_SYS_NAME):
    print("No thing_collection_sys_name filtering applied; using ALL collections.")
else:
    things_datastreams_df = things_datastreams_df[
        things_datastreams_df["thing_collection_sys_name"]
        .fillna("")
        .astype(str)
        .str.strip()
        .isin(target_collection_sys_names)
    ].copy()
    print(
        "Filtered datastreams to "
        f"thing_collection_sys_name={target_collection_sys_names}."
    )


# Save the dataframe so it can be opened or reused later.
try:
    things_datastreams_df.to_csv(OUTPUT_PATH, index=False)
    saved_main_csv = True
except PermissionError:
    print(f"Could not save {OUTPUT_PATH}. Close the file if it is open.")
    saved_main_csv = False


def normalize_spacing_unit(spacing_unit):
    if pd.isna(spacing_unit):
        return ""
    return str(spacing_unit).strip().lower()


def get_dvrt_url(station_id, spacing_unit):
    station_id = str(station_id).split(".")[0]
    spacing = normalize_spacing_unit(spacing_unit)

    if spacing == "days":
        return f"{DVRT_DAILY_CHART_URL}?station_id={station_id}&f=json"

    if spacing in ["hours", "minutes"]:
        return f"{DVRT_REALTIME_CHART_URL}?station_id={station_id}&f=json-all"

    raise ValueError(f"Unsupported intended_time_spacing_unit={spacing_unit!r}")


def get_dvrt_plot_url(station_id, spacing_unit):
    station_id = str(station_id).split(".")[0]
    spacing = normalize_spacing_unit(spacing_unit)

    if spacing == "days":
        return f"{DVRT_DAILY_CHART_URL}?STATION_ID={station_id}"

    if spacing in ["hours", "minutes"]:
        return f"{DVRT_REALTIME_CHART_URL}?STATION_ID={station_id}"

    return ""


def get_local_comparison_plot_url(station_id):
    station_id = str(station_id).split(".")[0]
    return (
        f"{LOCAL_COMPARISON_PLOT_URL}?station_id={station_id}"
        f"&start_date={LOCAL_COMPARISON_START_DATE}"
    )


def get_dvrt_timeseries(station_id, spacing_unit):
    url = get_dvrt_url(station_id, spacing_unit)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    text = response.text.replace("\t", "")
    return json.loads(text)


def get_hydroserver_observations(hs, datastream_uid):
    all_observations = []
    page = 1

    while True:
        response = hs.request(
            "get",
            f"{hs.base_route}/datastreams/{datastream_uid}/observations",
            params={"page": page, "page_size": OBSERVATION_PAGE_SIZE},
        )
        data = response.json()

        if isinstance(data, dict) and "results" in data:
            observations = data["results"]
        elif isinstance(data, dict) and "items" in data:
            observations = data["items"]
        elif isinstance(data, list):
            observations = data
        else:
            observations = []

        all_observations.extend(observations)

        if len(observations) < OBSERVATION_PAGE_SIZE:
            break

        page += 1

    return all_observations


def normalize_datetime_column(series, spacing_unit, source):
    parsed = pd.to_datetime(series, errors="coerce")

    if normalize_spacing_unit(spacing_unit) == "days":
        return parsed.dt.strftime("%Y-%m-%d")

    if source == "dvrt":
        parsed = parsed.dt.tz_localize(
            "America/Denver",
            ambiguous=False,
            nonexistent="shift_forward",
        ).dt.tz_convert("UTC")
    else:
        parsed = pd.to_datetime(series, utc=True, errors="coerce")

    return parsed.dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def choose_value_column(df, datastream_name=None):
    excluded_columns = {
        "date",
        "datetime",
        "phenomenontime",
        "phenomenon_time",
        "time",
        "timestamp",
        "station_id",
        "stationid",
        "id",
        "year",
        "month",
        "day",
    }
    name = "" if pd.isna(datastream_name) else str(datastream_name).strip().lower()
    columns_by_normalized_name = {
        str(column).strip().lower(): column for column in df.columns
    }

    name_hints = [
        (
            ["gage height", "gageheight", "stage"],
            ["gageheight", "gage_height", "stage", "elevation"],
        ),
        (["discharge", "flow"], ["discharge", "flow", "value", "rv", "rv_01"]),
        (["elevation"], ["elevation", "stage", "gageheight", "value"]),
        (["volume"], ["volume", "value"]),
    ]

    for datastream_terms, column_terms in name_hints:
        if any(term in name for term in datastream_terms):
            for normalized_column, original_column in columns_by_normalized_name.items():
                if any(
                    term == normalized_column or term in normalized_column
                    for term in column_terms
                ):
                    return original_column

    preferred_columns = [
        "value",
        "result",
        "resultvalue",
        "result_value",
        "measurement",
        "gageheight",
        "gage_height",
        "elevation",
        "stage",
        "discharge",
        "flow",
        "rv",
        "rv_01",
    ]
    for preferred_column in preferred_columns:
        if preferred_column in columns_by_normalized_name:
            return columns_by_normalized_name[preferred_column]

    for column in df.columns:
        normalized_column = str(column).strip().lower()
        if normalized_column in excluded_columns:
            continue
        numeric_values = pd.to_numeric(df[column], errors="coerce")
        if numeric_values.notna().any():
            return column

    return df.columns[1] if len(df.columns) > 1 else df.columns[0]


def make_timeseries_dataframe(data, spacing_unit, source, datastream_name=None):
    if isinstance(data, dict):
        if "data" in data:
            rows = data["data"]
        elif "values" in data:
            rows = data["values"]
        elif "results" in data:
            rows = data["results"]
        elif "observations" in data:
            rows = data["observations"]
        elif "value" in data:
            rows = data["value"]
        else:
            rows = data
    else:
        rows = data

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    date_column = None
    value_column = None

    for column in df.columns:
        lower_column = str(column).lower()
        if date_column is None and lower_column in [
            "date",
            "datetime",
            "phenomenontime",
            "phenomenon_time",
            "time",
            "timestamp",
        ]:
            date_column = column
        if value_column is None:
            value_column = choose_value_column(df, datastream_name)

    if date_column is None:
        date_column = df.columns[0]

    if value_column is None:
        value_column = choose_value_column(df, datastream_name)

    df = df[[date_column, value_column]].copy()
    df.columns = ["date", "value"]
    df["date"] = normalize_datetime_column(df["date"], spacing_unit, source)
    df["value"] = pd.to_numeric(df["value"], errors="coerce").round(6)
    df = df.dropna().sort_values("date").reset_index(drop=True)

    return df


def get_different_record_counts(first_df, second_df):
    compare_columns = ["date", "value"]
    empty_index = pd.MultiIndex.from_tuples([], names=compare_columns)

    if first_df.empty:
        first_counts = pd.Series(dtype="int64", index=empty_index)
    else:
        first_counts = first_df.value_counts(compare_columns)

    if second_df.empty:
        second_counts = pd.Series(dtype="int64", index=empty_index)
    else:
        second_counts = second_df.value_counts(compare_columns)

    all_records = first_counts.index.union(second_counts.index)
    return (
        first_counts.reindex(all_records, fill_value=0)
        - second_counts.reindex(all_records, fill_value=0)
    ).abs()


def count_different_records(first_df, second_df):
    different_counts = get_different_record_counts(first_df, second_df)
    return int(different_counts.sum())


def get_record_difference_summary(first_df, second_df):
    missing_value_count = 0
    different_value_count = 0
    missing_value_dates = []
    different_value_dates = []

    first_by_date = (
        {date: group["value"] for date, group in first_df.groupby("date")}
        if not first_df.empty
        else {}
    )
    second_by_date = (
        {date: group["value"] for date, group in second_df.groupby("date")}
        if not second_df.empty
        else {}
    )

    for date in sorted(set(first_by_date).union(second_by_date)):
        first_values = first_by_date.get(date, pd.Series(dtype="float64"))
        second_values = second_by_date.get(date, pd.Series(dtype="float64"))

        if first_values.empty or second_values.empty:
            missing_value_count += max(len(first_values), len(second_values))
            missing_value_dates.append(str(date))
            continue

        first_counts = first_values.value_counts()
        second_counts = second_values.value_counts()
        all_values = first_counts.index.union(second_counts.index)
        unmatched_first_count = int(
            (
                first_counts.reindex(all_values, fill_value=0)
                - second_counts.reindex(all_values, fill_value=0)
            ).clip(lower=0).sum()
        )
        unmatched_second_count = int(
            (
                second_counts.reindex(all_values, fill_value=0)
                - first_counts.reindex(all_values, fill_value=0)
            ).clip(lower=0).sum()
        )

        different_count_for_date = min(unmatched_first_count, unmatched_second_count)
        missing_count_for_date = abs(unmatched_first_count - unmatched_second_count)

        if different_count_for_date:
            different_value_count += different_count_for_date
            different_value_dates.append(str(date))

        if missing_count_for_date:
            missing_value_count += missing_count_for_date
            missing_value_dates.append(str(date))

    return {
        "missing_value_count": missing_value_count,
        "different_value_count": different_value_count,
        "missing_value_dates": ", ".join(missing_value_dates)
        if 0 < missing_value_count < 10
        else "",
        "different_value_dates": ", ".join(different_value_dates)
        if 0 < different_value_count < 10
        else "",
    }


def get_different_record_dates(first_df, second_df):
    different_counts = get_different_record_counts(first_df, second_df)
    different_records = different_counts[different_counts > 0]
    different_record_count = int(different_records.sum())

    if different_record_count == 0 or different_record_count >= 10:
        return ""

    dates = []
    seen_dates = set()
    for record in different_records.index:
        date = str(record[0])
        if date not in seen_dates:
            dates.append(date)
            seen_dates.add(date)

    return ", ".join(dates)


def iter_batches(rows, batch_size):
    for start_index in range(0, len(rows), batch_size):
        yield rows[start_index : start_index + batch_size]


def save_comparison_rows(comparison_rows):
    comparison_df = pd.DataFrame(comparison_rows)
    if not comparison_df.empty and "different_record_count" in comparison_df.columns:
        comparison_df["identical"] = (
            comparison_df["different_record_count"].fillna(-1).eq(0)
            & comparison_df["error"].fillna("").eq("")
        )
    comparison_df.to_csv(COMPARE_OUTPUT_PATH, index=False)
    return comparison_df


def compare_usbr_datastreams():
    comparison_rows = []
    target_collection_sys_names = normalize_target_collection_sys_names(
        TARGET_COLLECTION_SYS_NAME
    )

    if target_collection_sys_names_include_all(TARGET_COLLECTION_SYS_NAME):
        usbr_rows = things_datastreams_df
        print("No thing_collection_sys_name filtering applied; comparing ALL collections.")
    else:
        usbr_rows = things_datastreams_df[
            things_datastreams_df["thing_collection_sys_name"].isin(
                target_collection_sys_names
            )
        ]

    if usbr_rows.empty:
        print(
            "No datastreams matched "
            f"thing_collection_sys_name={TARGET_COLLECTION_SYS_NAME}."
        )

    rows_to_compare = [
        row
        for _, row in usbr_rows.iterrows()
        if not pd.isna(row["datastream_STATION_ID"])
        and not pd.isna(row["datastream_uid"])
    ]
    total_batches = (
        (len(rows_to_compare) + API_BATCH_SIZE - 1) // API_BATCH_SIZE
        if rows_to_compare
        else 0
    )

    for batch_number, batch_rows in enumerate(
        iter_batches(rows_to_compare, API_BATCH_SIZE),
        start=1,
    ):
        print(
            f"Processing API batch {batch_number}/{total_batches} "
            f"({len(batch_rows)} datastreams)"
        )

        for row in batch_rows:
            station_id = row["datastream_STATION_ID"]
            normalized_station_id = str(station_id).split(".")[0]
            collection_sys_name = row["thing_collection_sys_name"]
            thing_uid = row["thing_uid"]
            thing_name = row["thing_name"]
            datastream_uid = row["datastream_uid"]
            datastream_name = row["datastream_name"]
            spacing_unit = row.get("datastream_intended_time_spacing_unit")
            dvrt_plot_link = get_dvrt_plot_url(station_id, spacing_unit)
            local_comparison_plot_link = get_local_comparison_plot_url(station_id)

            try:
                dvrt_data = get_dvrt_timeseries(station_id, spacing_unit)
                hydroserver_data = get_hydroserver_observations(
                    hs_api,
                    datastream_uid,
                )

                dvrt_df = make_timeseries_dataframe(
                    dvrt_data,
                    spacing_unit,
                    source="dvrt",
                    datastream_name=datastream_name,
                )
                hydroserver_df = make_timeseries_dataframe(
                    hydroserver_data,
                    spacing_unit,
                    source="hydroserver",
                    datastream_name=datastream_name,
                )

                different_record_count = count_different_records(
                    dvrt_df,
                    hydroserver_df,
                )
                difference_summary = get_record_difference_summary(
                    dvrt_df,
                    hydroserver_df,
                )
                identical = different_record_count == 0

                comparison_rows.append(
                    {
                        "collection_sys_name": collection_sys_name,
                        "station_id": normalized_station_id,
                        "thing_uid": thing_uid,
                        "thing_name": thing_name,
                        "datastream_uid": datastream_uid,
                        "datastream_name": datastream_name,
                        "spacing_unit": spacing_unit,
                        "hydroserver_datastream_link": (
                            f"{HYDROSERVER_VISUALIZE_URL}"
                            f"?sites={thing_uid}&datastreams={datastream_uid}"
                        ),
                        "local_comparison_plot_link": local_comparison_plot_link,
                        "dvrt_plot_link": dvrt_plot_link,
                        "dvrt_observation_count": len(dvrt_df),
                        "hydroserver_observation_count": len(hydroserver_df),
                        "different_record_count": different_record_count,
                        "missing_value_count": difference_summary.get(
                            "missing_value_count"
                        ),
                        "different_value_count": difference_summary.get(
                            "different_value_count"
                        ),
                        "different_record_dates": get_different_record_dates(
                            dvrt_df,
                            hydroserver_df,
                        ),
                        "missing_value_dates": difference_summary.get(
                            "missing_value_dates",
                            "",
                        ),
                        "different_value_dates": difference_summary.get(
                            "different_value_dates",
                            "",
                        ),
                        "identical": identical,
                        "error": "",
                    }
                )

                print(
                    f"{station_id} | {datastream_uid} | "
                    f"spacing={spacing_unit} | "
                    f"identical={identical}"
                )

            except Exception as error:
                comparison_rows.append(
                    {
                        "collection_sys_name": collection_sys_name,
                        "station_id": normalized_station_id,
                        "thing_uid": thing_uid,
                        "thing_name": thing_name,
                        "datastream_uid": datastream_uid,
                        "datastream_name": datastream_name,
                        "spacing_unit": spacing_unit,
                        "hydroserver_datastream_link": (
                            f"{HYDROSERVER_VISUALIZE_URL}"
                            f"?sites={thing_uid}&datastreams={datastream_uid}"
                        ),
                        "local_comparison_plot_link": local_comparison_plot_link,
                        "dvrt_plot_link": dvrt_plot_link,
                        "dvrt_observation_count": None,
                        "hydroserver_observation_count": None,
                        "different_record_count": None,
                        "missing_value_count": None,
                        "different_value_count": None,
                        "different_record_dates": "",
                        "missing_value_dates": "",
                        "different_value_dates": "",
                        "identical": False,
                        "error": str(error),
                    }
                )
                print(f"{station_id} | {datastream_uid} | ERROR: {error}")

        try:
            save_comparison_rows(comparison_rows)
            print(f"Saved partial comparison results to: {COMPARE_OUTPUT_PATH}")
        except PermissionError:
            print(f"Could not save {COMPARE_OUTPUT_PATH}. Close the file if it is open.")

        if batch_number < total_batches:
            print(f"Waiting {API_BATCH_WAIT_SECONDS} seconds before next API batch...")
            time.sleep(API_BATCH_WAIT_SECONDS)

    try:
        comparison_df = save_comparison_rows(comparison_rows)
        print(f"Saved comparison results to: {COMPARE_OUTPUT_PATH}")
    except PermissionError:
        print(f"Could not save {COMPARE_OUTPUT_PATH}. Close the file if it is open.")

    return comparison_df

print(f"Workspace: {WORKSPACE_NAME}")
print(f"Workspace ID: {WORKSPACE_ID}")
print(f"Things: {len(df_things)}")
print(f"Datastreams: {len(df_datastreams)}")
print(f"Rows in final dataframe: {len(things_datastreams_df)}")
if saved_main_csv:
    print(f"Saved to: {OUTPUT_PATH}")

comparison_df = compare_usbr_datastreams()
