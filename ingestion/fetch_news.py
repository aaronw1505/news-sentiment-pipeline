"""
News ingestion + sentiment classification.

Step 1 of the News Sentiment Analytics Pipeline:

    NewsAPI  ->  Claude (claude-haiku-4-5)  ->  DuckDB (raw.raw_headlines)

For each *new* headline we ask Claude to:
  * classify sentiment as Positive / Neutral / Negative
  * write a one-sentence summary

The classification instructions live in a single cached system prompt, so
every headline after the first re-uses the same cached prefix. The run summary
prints the cache token counters so you can confirm caching is working.

Run from the repo root:

    python ingestion/fetch_news.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone
from typing import Literal

import duckdb
import requests
from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

# --- Configuration -----------------------------------------------------------

NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
DUCKDB_PATH = os.environ.get("DUCKDB_PATH", "data/news.duckdb")
NEWS_COUNTRY = os.environ.get("NEWS_COUNTRY", "us")
NEWS_QUERY = os.environ.get("NEWS_QUERY", "").strip()
NEWS_PAGE_SIZE = int(os.environ.get("NEWS_PAGE_SIZE", "50"))
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")

NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"


# --- Structured output schema ------------------------------------------------
# Claude is forced to return JSON matching this shape (no fragile text parsing).
# The Literal becomes a JSON-schema enum, so sentiment is always one of three
# exact values.

class HeadlineSentiment(BaseModel):
    sentiment: Literal["Positive", "Neutral", "Negative"]
    summary: str


# --- Cached classification prompt --------------------------------------------
# Sent on every request with `cache_control: ephemeral`. The render order is
# tools -> system -> messages, so this system block forms a stable prefix that
# Claude serves from cache across headlines (subject to the model's minimum
# cacheable prefix size). Keep this text BYTE-STABLE — any edit invalidates the
# cache for the rest of the run.

SYSTEM_PROMPT = """You are a news sentiment classifier for a financial-analytics pipeline. \
You read a single news headline (and an optional short description) and return a structured \
judgement about its sentiment plus a concise summary.

Classify the overall sentiment of the news into exactly one of three labels:

- "Positive": the event is favorable, encouraging, or good news for the primary subject or \
the broader public. Examples: economic growth, successful launches, recoveries, awards, \
breakthroughs, deals closing, records broken in a good way.
- "Negative": the event is unfavorable, harmful, or bad news. Examples: disasters, deaths, \
layoffs, crashes, scandals, conflicts, losses, declines, crime, fraud, outages.
- "Neutral": the event is primarily factual, procedural, or mixed, with no clear positive or \
negative valence. Examples: scheduling announcements, routine appointments, balanced reports, \
'X said Y' statements, weather updates, sports fixtures without a result.

Guidelines:
- Judge the sentiment of the *event itself*, not the tone of the writing.
- If a headline is genuinely ambiguous or balanced, prefer "Neutral".
- Do not let a single emotionally charged word override the overall meaning.
- Base your judgement only on the text provided; do not invent facts.

For the summary:
- Write exactly ONE sentence (no more) that captures the core of the headline.
- Be factual and neutral in wording; do not add opinion or speculation.
- Keep it under ~30 words and self-contained (a reader who never saw the headline \
should understand the gist).

Worked examples:

Headline: "Tech giant reports record quarterly profit, beating analyst expectations"
-> sentiment: Positive
-> summary: A major technology company posted record quarterly profit that exceeded analyst forecasts.

Headline: "Hundreds of flights cancelled as winter storm grounds airline operations"
-> sentiment: Negative
-> summary: A winter storm forced the cancellation of hundreds of flights and disrupted airline operations.

Headline: "Central bank to announce interest rate decision next Wednesday"
-> sentiment: Neutral
-> summary: The central bank scheduled an interest rate decision announcement for next Wednesday.

Headline: "Startup raises $50M to expand clean-energy battery manufacturing"
-> sentiment: Positive
-> summary: A startup secured $50 million in funding to scale up clean-energy battery production.

Headline: "Regulators open investigation into automaker over safety complaints"
-> sentiment: Negative
-> summary: Regulators launched an investigation into an automaker following safety complaints.

Headline: "City council reviews proposed changes to downtown parking rules"
-> sentiment: Neutral
-> summary: The city council is reviewing proposed changes to downtown parking regulations.

Return only the structured fields requested."""


# --- NewsAPI -----------------------------------------------------------------

def fetch_headlines() -> list[dict]:
    """Pull top headlines from NewsAPI (free tier)."""
    params = {
        "apiKey": NEWSAPI_KEY,
        "pageSize": NEWS_PAGE_SIZE,
    }
    if NEWS_QUERY:
        params["q"] = NEWS_QUERY
    else:
        # `q` and `country` can't always be combined on the free tier, so only
        # send country when there's no keyword query.
        params["country"] = NEWS_COUNTRY

    resp = requests.get(NEWSAPI_URL, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {payload.get('message', payload)}")

    articles = payload.get("articles", [])
    print(f"Fetched {len(articles)} headlines from NewsAPI.")
    return articles


# --- DuckDB ------------------------------------------------------------------

def init_db(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS raw.raw_headlines (
            id           VARCHAR PRIMARY KEY,
            source       VARCHAR,
            author       VARCHAR,
            title        VARCHAR,
            description  VARCHAR,
            url          VARCHAR,
            published_at TIMESTAMP,
            sentiment    VARCHAR,
            summary      VARCHAR,
            fetched_at   TIMESTAMP
        )
        """
    )


def existing_ids(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute("SELECT id FROM raw.raw_headlines").fetchall()
    return {r[0] for r in rows}


def make_id(url: str | None, title: str) -> str:
    """Stable id derived from the URL (falls back to the title)."""
    key = (url or title or "").encode("utf-8")
    return hashlib.sha256(key).hexdigest()[:16]


def parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- Claude classification ---------------------------------------------------

def classify(client: Anthropic, title: str, description: str | None):
    """Return the full Claude response (so the caller can read usage), or None."""
    user_content = f"Headline: {title}"
    if description:
        user_content += f"\n\nDescription: {description}"

    try:
        return client.messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=256,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
            output_format=HeadlineSentiment,
        )
    except Exception as exc:  # noqa: BLE001 - log and skip a single bad headline
        print(f"  ! classification failed: {exc}", file=sys.stderr)
        return None


# --- Orchestration -----------------------------------------------------------

def main() -> int:
    if not NEWSAPI_KEY:
        print("ERROR: NEWSAPI_KEY is not set (see .env.example).", file=sys.stderr)
        return 1
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY is not set (see .env.example).", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(DUCKDB_PATH) or ".", exist_ok=True)

    articles = fetch_headlines()
    if not articles:
        print("No headlines returned; nothing to do.")
        return 0

    client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    con = duckdb.connect(DUCKDB_PATH)
    init_db(con)
    seen = existing_ids(con)

    rows: list[tuple] = []
    fetched_at = datetime.now(timezone.utc)
    cache_read = cache_write = uncached = 0
    classified = skipped = 0

    for article in articles:
        title = (article.get("title") or "").strip()
        if not title or title == "[Removed]":
            continue

        url = article.get("url")
        headline_id = make_id(url, title)
        if headline_id in seen:
            skipped += 1
            continue
        seen.add(headline_id)

        description = (article.get("description") or "").strip() or None
        resp = classify(client, title, description)
        if resp is None:
            continue

        result: HeadlineSentiment = resp.parsed_output
        usage = resp.usage
        cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0
        uncached += getattr(usage, "input_tokens", 0) or 0
        classified += 1

        source = (article.get("source") or {}).get("name")
        rows.append(
            (
                headline_id,
                source,
                article.get("author"),
                title,
                description,
                url,
                parse_published(article.get("publishedAt")),
                result.sentiment,
                result.summary,
                fetched_at,
            )
        )
        print(f"  [{result.sentiment:>8}] {title[:80]}")

    if rows:
        con.executemany(
            """
            INSERT INTO raw.raw_headlines
                (id, source, author, title, description, url,
                 published_at, sentiment, summary, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
            """,
            rows,
        )

    total = con.execute("SELECT COUNT(*) FROM raw.raw_headlines").fetchone()[0]
    con.close()  # flushes the WAL and releases the file lock for dbt

    print("\n--- Run summary ---------------------------------------------")
    print(f"  classified this run : {classified}")
    print(f"  skipped (already in DB): {skipped}")
    print(f"  rows in raw.raw_headlines: {total}")
    print(f"  cache READ tokens   : {cache_read:>8}  (served from cache, ~0.1x cost)")
    print(f"  cache WRITE tokens  : {cache_write:>8}  (written to cache, ~1.25x cost)")
    print(f"  uncached tokens     : {uncached:>8}  (full price)")
    print("-------------------------------------------------------------")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
