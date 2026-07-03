import inspect
from uuid import UUID

import pandas as pd
import requests

# =========================================================
# High-level logic
# =========================================================
# Shared helpers for reading HydroServer list endpoints into pandas.
#
# These utilities convert HydroServer model objects to serializable values,
# flatten nested fields such as tags, cast mostly numeric columns, handle
# paged list calls with safe default sorting and retry behavior, and provide
# a small duplicate-check helper used by the migration scripts.


def _to_serializable(value):
    """Convert non-serializable values into DataFrame-friendly values."""
    if isinstance(value, UUID):
        return str(value)
    return value


def _item_to_dict(item):
    """Convert one HydroServer object into a plain dictionary."""
    return {
        attr: _to_serializable(value)
        for attr, value in vars(item).items()
        if not attr.startswith("_")
    }


def _flatten_dict_columns(df, prefix_dict_columns=False):
    """
    Flatten columns that contain dictionaries.

    Parameters
    ----------
    df : pd.DataFrame
        Input dataframe.
    prefix_dict_columns : bool, default False
        If True, expanded dictionary keys are prefixed with the source column name.
        Example: tags.Status -> tags_Status

    Returns
    -------
    pd.DataFrame
    """
    df = df.copy()

    dict_cols = [
        col for col in df.columns
        if df[col].apply(lambda x: isinstance(x, dict)).any()
    ]

    for col in dict_cols:
        mask = df[col].apply(lambda x: isinstance(x, dict))

        expanded = pd.json_normalize(df.loc[mask, col])
        expanded.index = df.loc[mask].index

        if prefix_dict_columns:
            expanded.columns = [f"{col}_{c}" for c in expanded.columns]
        else:
            remaining_columns = set(df.columns) - {col}
            expanded.columns = [
                f"{col}_{c}" if c in remaining_columns else c
                for c in expanded.columns
            ]

        df = df.drop(columns=[col]).join(expanded, how="left")

    return df


def _auto_cast_numeric_columns(df, threshold=0.9):
    """
    Convert object columns to numeric when most non-null values are numeric.

    Parameters
    ----------
    df : pd.DataFrame
    threshold : float, default 0.9
        Minimum share of non-null values that must successfully convert.

    Returns
    -------
    pd.DataFrame
    """
    df = df.copy()

    for col in df.columns:
        if df[col].dtype != "object":
            continue

        converted = pd.to_numeric(df[col], errors="coerce")
        non_null_count = df[col].notna().sum()

        if non_null_count == 0:
            continue

        numeric_count = converted.notna().sum()
        if numeric_count / non_null_count >= threshold:
            df[col] = converted

    return df


def _should_apply_default_sort(list_fn, default_sort_endpoints):
    """Check whether default sorting should be applied for this endpoint."""
    fn_id = " ".join([
        getattr(list_fn, "__qualname__", ""),
        getattr(list_fn, "__name__", ""),
        repr(list_fn),
    ]).lower()

    return any(endpoint in fn_id for endpoint in default_sort_endpoints)


def _call_list_with_optional_order(list_fn, order_by=None, **kwargs):
    """Call HydroServer list function with or without order_by."""
    call_kwargs = dict(kwargs)
    if order_by is not None:
        call_kwargs["order_by"] = order_by
    return list_fn(**call_kwargs)


def _fetch_all_pages(list_fn, order_by=None, **list_kwargs):
    """
    Fetch all pages from a HydroServer .list() endpoint.

    Uses fetch_all=True when supported; otherwise falls back to manual pagination.
    """
    sig = inspect.signature(list_fn)
    supports_fetch_all = "fetch_all" in sig.parameters

    if supports_fetch_all:
        collection = _call_list_with_optional_order(
            list_fn,
            order_by=order_by,
            fetch_all=True,
            **list_kwargs
        )
        return collection.items

    items = []
    page = 1

    while True:
        collection = _call_list_with_optional_order(
            list_fn,
            order_by=order_by,
            page=page,
            **list_kwargs
        )

        page_items = collection.items
        items.extend(page_items)

        total_pages = getattr(collection, "total_pages", None)
        page_size = getattr(collection, "page_size", None)

        if total_pages is not None:
            if page >= total_pages:
                break
        elif page_size is not None:
            if len(page_items) < page_size:
                break
        else:
            if not page_items:
                break

        page += 1

    return items


def _fetch_items_with_sort_fallback(list_fn, fetch_all, order_by, **list_kwargs):
    """
    Fetch items, retrying once without sorting if HydroServer rejects order_by with 422.
    """
    candidate_orderings = [order_by, None] if order_by is not None else [None]
    last_error = None

    for candidate_order_by in candidate_orderings:
        try:
            if fetch_all:
                return _fetch_all_pages(
                    list_fn,
                    order_by=candidate_order_by,
                    **list_kwargs
                )

            collection = _call_list_with_optional_order(
                list_fn,
                order_by=candidate_order_by,
                **list_kwargs
            )
            return collection.items

        except requests.HTTPError as e:
            last_error = e
            response = getattr(e, "response", None)

            # Retry only on validation-type sort failures
            if response is None or response.status_code != 422:
                raise

    raise last_error


def hydro_list_to_flat_df(
    list_fn,
    fetch_all=True,
    flatten_dicts=True,
    prefix_dict_columns=False,
    order_by=None,
    auto_cast_numeric=True,
    numeric_threshold=0.9,
    default_sort_endpoints=("things", "datastreams"),
    default_order_by_for_sorted_endpoints=("name",),
    **list_kwargs
):
    """
    Convert a HydroServer .list() endpoint response into a flat pandas DataFrame.

    Behavior
    --------
    - Fetches all records by default.
    - Applies default sorting only to selected endpoints such as Things and Datastreams.
    - Retries once without sorting if order_by triggers a 422 validation error.
    - Flattens one-level dictionary columns like tags or properties.
    - Converts mostly numeric text columns into numeric dtype.

    Parameters
    ----------
    list_fn : callable
        HydroServer .list method, e.g. hs_api.things.list
    fetch_all : bool, default True
        If True, fetch all records across all pages.
    flatten_dicts : bool, default True
        If True, flatten dictionary columns.
    prefix_dict_columns : bool, default False
        If True, prefix flattened keys with their source column name.
    order_by : list[str] or None, default None
        Explicit sort fields.
    auto_cast_numeric : bool, default True
        If True, convert mostly numeric text columns.
    numeric_threshold : float, default 0.9
        Threshold for numeric auto-casting.
    default_sort_endpoints : tuple[str], default ("things", "datastreams")
        Endpoint names that should receive default sorting.
    default_order_by_for_sorted_endpoints : tuple[str], default ("name",)
        Default sort field used for eligible endpoints.
    **list_kwargs
        Extra arguments passed to the HydroServer list function.

    Returns
    -------
    pd.DataFrame
    """
    if order_by is None and _should_apply_default_sort(list_fn, default_sort_endpoints):
        order_by = [default_order_by_for_sorted_endpoints[0]]

    items = _fetch_items_with_sort_fallback(
        list_fn,
        fetch_all=fetch_all,
        order_by=order_by,
        **list_kwargs
    )

    df = pd.DataFrame([_item_to_dict(item) for item in items])

    if flatten_dicts and not df.empty:
        df = _flatten_dict_columns(df, prefix_dict_columns=prefix_dict_columns)

    if auto_cast_numeric and not df.empty:
        df = _auto_cast_numeric_columns(df, threshold=numeric_threshold)

    return df


def check_for_duplicates(df):
    """
    Check whether a DataFrame contains duplicate rows.

    Returns
    -------
    str | None
        'FYI Duplicates Found in the DF' if duplicates exist, otherwise None.
    """
    if df.duplicated().any():
        return "FYI Duplicates Found in the DF"
    return None
