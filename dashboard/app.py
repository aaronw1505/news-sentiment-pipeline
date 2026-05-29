"""
Streamlit dashboard for the News Sentiment Analytics Pipeline.

Reads the dbt-built analytics tables from DuckDB and renders:
  * headline volume + sentiment trends over time
  * top news sources by volume and sentiment
  * the most recent classified headlines

Run from the repo root:

    streamlit run dashboard/app.py
"""

from __future__ import annotations

import os

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

DUCKDB_PATH = os.environ.get("DUCKDB_PATH", "data/news.duckdb")

SENTIMENT_COLORS = {
    "Positive": "#2ca02c",
    "Neutral": "#7f7f7f",
    "Negative": "#d62728",
}

st.set_page_config(
    page_title="News Sentiment Analytics",
    page_icon="📰",
    layout="wide",
)


@st.cache_data(ttl=300)
def query(sql: str) -> pd.DataFrame:
    """Run a read-only query against the DuckDB analytics database."""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        return con.execute(sql).fetchdf()
    finally:
        con.close()


def data_available() -> bool:
    if not os.path.exists(DUCKDB_PATH):
        return False
    try:
        query("SELECT 1 FROM main.fct_sentiment_by_day LIMIT 1")
        return True
    except Exception:
        return False


# --- Header ------------------------------------------------------------------

st.title("📰 News Sentiment Analytics")
st.caption(
    "Live news headlines classified by Claude (claude-haiku-4-5), "
    "transformed with dbt, served from DuckDB."
)

if not data_available():
    st.warning(
        "No analytics data found yet. Run the pipeline first:\n\n"
        "1. `python ingestion/fetch_news.py`\n"
        "2. `dbt build --project-dir dbt_project --profiles-dir dbt_project`"
    )
    st.stop()

daily = query("SELECT * FROM main.fct_sentiment_by_day ORDER BY published_date")
sources = query("SELECT * FROM main.dim_source_sentiment ORDER BY total_headlines DESC")
recent = query(
    """
    SELECT published_at, source_name, title, sentiment, summary, url
    FROM main.stg_headlines
    ORDER BY published_at DESC NULLS LAST
    LIMIT 50
    """
)

# --- KPI row -----------------------------------------------------------------

total_headlines = int(daily["total_headlines"].sum())
total_positive = int(daily["positive_count"].sum())
total_negative = int(daily["negative_count"].sum())
latest_score = float(daily["avg_sentiment_score"].iloc[-1]) if len(daily) else 0.0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total headlines", f"{total_headlines:,}")
c2.metric("Positive", f"{(total_positive / total_headlines * 100):.0f}%" if total_headlines else "—")
c3.metric("Negative", f"{(total_negative / total_headlines * 100):.0f}%" if total_headlines else "—")
c4.metric("Latest avg score", f"{latest_score:+.2f}", help="Range -1 (all negative) to +1 (all positive)")

st.divider()

# --- Trends over time --------------------------------------------------------

left, right = st.columns(2)

with left:
    st.subheader("Sentiment volume over time")
    trend = daily.melt(
        id_vars="published_date",
        value_vars=["positive_count", "neutral_count", "negative_count"],
        var_name="sentiment",
        value_name="count",
    )
    trend["sentiment"] = trend["sentiment"].map(
        {"positive_count": "Positive", "neutral_count": "Neutral", "negative_count": "Negative"}
    )
    fig = px.bar(
        trend,
        x="published_date",
        y="count",
        color="sentiment",
        color_discrete_map=SENTIMENT_COLORS,
        labels={"published_date": "Date", "count": "Headlines"},
    )
    fig.update_layout(legend_title="", margin=dict(t=10, b=0, l=0, r=0))
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("Average sentiment score")
    fig = px.line(
        daily,
        x="published_date",
        y="avg_sentiment_score",
        markers=True,
        labels={"published_date": "Date", "avg_sentiment_score": "Avg score"},
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.update_yaxes(range=[-1, 1])
    fig.update_layout(margin=dict(t=10, b=0, l=0, r=0))
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- Top sources -------------------------------------------------------------

st.subheader("Top sources by volume")
top_sources = sources.head(15).melt(
    id_vars="source_name",
    value_vars=["positive_count", "neutral_count", "negative_count"],
    var_name="sentiment",
    value_name="count",
)
top_sources["sentiment"] = top_sources["sentiment"].map(
    {"positive_count": "Positive", "neutral_count": "Neutral", "negative_count": "Negative"}
)
fig = px.bar(
    top_sources,
    x="count",
    y="source_name",
    color="sentiment",
    color_discrete_map=SENTIMENT_COLORS,
    orientation="h",
    labels={"count": "Headlines", "source_name": ""},
)
fig.update_layout(
    legend_title="",
    yaxis=dict(categoryorder="total ascending"),
    margin=dict(t=10, b=0, l=0, r=0),
    height=500,
)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- Recent headlines --------------------------------------------------------

st.subheader("Recent headlines")
sentiment_filter = st.multiselect(
    "Filter by sentiment",
    options=["Positive", "Neutral", "Negative"],
    default=["Positive", "Neutral", "Negative"],
)
view = recent[recent["sentiment"].isin(sentiment_filter)].copy()
st.dataframe(
    view,
    use_container_width=True,
    hide_index=True,
    column_config={
        "published_at": st.column_config.DatetimeColumn("Published", format="YYYY-MM-DD HH:mm"),
        "source_name": "Source",
        "title": "Headline",
        "sentiment": "Sentiment",
        "summary": "Summary",
        "url": st.column_config.LinkColumn("Link", display_text="open"),
    },
)
