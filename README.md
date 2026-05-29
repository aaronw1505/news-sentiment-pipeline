# 📰 News Sentiment Analytics Pipeline

An end-to-end data pipeline that pulls live news headlines, classifies their
sentiment with an LLM, models the data with dbt, and serves an interactive
analytics dashboard — fully automated on a daily schedule.

Built as a portfolio project to demonstrate a modern, production-style data
stack running entirely on free tiers.

---

## What it does

1. **Ingest** — pulls live top headlines from [NewsAPI](https://newsapi.org).
2. **Enrich** — sends each new headline to the **Claude API** (`claude-haiku-4-5`)
   to classify sentiment (Positive / Neutral / Negative) and extract a
   one-sentence summary, using **structured outputs** and **prompt caching**.
3. **Store** — writes structured results to a local **DuckDB** database.
4. **Transform** — runs **dbt** models on top of the raw data to produce clean
   analytics tables (daily trends, per-source breakdowns), with data tests.
5. **Serve** — a **Streamlit** dashboard visualizes sentiment trends over time,
   top sources, and recent headlines.
6. **Automate** — **GitHub Actions** runs the whole thing daily and commits the
   refreshed database back to the repo.

---

## Architecture

```
                    ┌──────────────┐
                    │   NewsAPI    │  live top headlines
                    └──────┬───────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │  ingestion/fetch_news.py              │
        │  • dedupe vs. existing rows           │
        │  • classify w/ Claude (haiku-4-5)     │
        │    – structured output (Pydantic)     │
        │    – cached system prompt             │
        └──────────────────┬───────────────────┘
                           │ raw.raw_headlines
                           ▼
                    ┌──────────────┐
                    │   DuckDB     │  data/news.duckdb
                    └──────┬───────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │  dbt (dbt-duckdb)                      │
        │  staging → marts + data tests         │
        │  • stg_headlines                      │
        │  • fct_sentiment_by_day               │
        │  • dim_source_sentiment               │
        └──────────────────┬───────────────────┘
                           │ main.* analytics tables
                           ▼
        ┌──────────────────────────────────────┐
        │  dashboard/app.py (Streamlit)         │
        └──────────────────────────────────────┘

        Orchestrated daily by  .github/workflows/pipeline.yml
```

---

## Tech stack

| Layer            | Tool                                   |
| ---------------- | -------------------------------------- |
| Ingestion        | Python, `requests`                     |
| Enrichment / LLM | Claude API (`anthropic` SDK, Haiku 4.5)|
| Storage          | DuckDB                                  |
| Transformation   | dbt (`dbt-duckdb`)                      |
| Dashboard        | Streamlit, Plotly                      |
| Orchestration    | GitHub Actions                         |
| Secrets          | `python-dotenv` / GitHub Secrets       |

---

## Project structure

```
news-sentiment-pipeline/
├── .github/workflows/pipeline.yml   # daily automation
├── ingestion/fetch_news.py          # NewsAPI → Claude → DuckDB
├── dbt_project/                     # dbt models, sources, tests, profile
│   ├── dbt_project.yml
│   ├── profiles.yml
│   └── models/
│       ├── staging/                 # stg_headlines (+ source + tests)
│       └── marts/                   # fct_sentiment_by_day, dim_source_sentiment
├── dashboard/app.py                 # Streamlit dashboard
├── data/                            # DuckDB lives here
├── requirements.txt
├── .env.example
└── README.md
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A free [NewsAPI](https://newsapi.org/register) key
- An [Anthropic API](https://console.anthropic.com/settings/keys) key

### 2. Install

```bash
git clone <your-repo-url>
cd news-sentiment-pipeline

python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure secrets

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

Edit `.env` and fill in `NEWSAPI_KEY` and `ANTHROPIC_API_KEY`.

---

## Run it locally

Run all commands **from the repo root** so the shared `data/news.duckdb` path
resolves consistently across the three steps.

```bash
# 1. Ingest + classify
python ingestion/fetch_news.py

# 2. Transform + test
dbt build --project-dir dbt_project --profiles-dir dbt_project

# 3. Launch the dashboard
streamlit run dashboard/app.py
```

The dashboard opens at http://localhost:8501.

> **Tip:** The ingestion script skips headlines already in the database, so
> Claude is only called on genuinely new articles — keeping cost and runtime
> low on repeated runs.

---

## How the Claude integration works

Each headline is classified with a single API call that uses two notable
Claude features:

- **Structured outputs** — the response is forced to match a Pydantic schema
  (`sentiment` as a strict enum, plus a `summary` string), so there is no
  brittle text parsing.
- **Prompt caching** — the classification instructions are sent as a cached
  system prompt (`cache_control: ephemeral`). Because the prompt prefix is
  identical across every headline, Claude can serve it from cache. The run
  summary prints `cache READ / WRITE / uncached` token counts so you can verify
  caching is active.

> Note on caching: a cached prefix only produces cache *reads* once it exceeds
> the model's minimum cacheable size (≈2K–4K tokens depending on model). The
> counters in the run summary make the behavior transparent either way.

See [ingestion/fetch_news.py](ingestion/fetch_news.py).

---

## Data model

| Model                   | Grain                | Purpose                                    |
| ----------------------- | -------------------- | ------------------------------------------ |
| `raw.raw_headlines`     | one row per headline | raw landing table written by ingestion     |
| `stg_headlines`         | one row per headline | cleaned + typed, with `published_date`      |
| `fct_sentiment_by_day`  | one row per day      | sentiment counts + avg score (-1…+1)        |
| `dim_source_sentiment`  | one row per source   | per-source sentiment breakdown by volume    |

dbt tests enforce uniqueness/not-null on keys and an `accepted_values` test on
the sentiment label.

---

## Automation (GitHub Actions)

The workflow in [.github/workflows/pipeline.yml](.github/workflows/pipeline.yml)
runs daily (and on demand), executing ingestion → `dbt build` → commit the
refreshed `data/news.duckdb` back to the repo.

Add two repository secrets under **Settings → Secrets and variables → Actions**:

- `NEWSAPI_KEY`
- `ANTHROPIC_API_KEY`

---

## Deploy the dashboard (Streamlit Community Cloud)

1. Push this repo to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) and create a new app
   pointing at `dashboard/app.py`.
3. Because the daily workflow commits `data/news.duckdb` to the repo, the
   deployed dashboard automatically picks up fresh data on each redeploy /
   refresh.

> No cloud database account required — DuckDB is a single file that travels with
> the repo.

---

## Possible extensions

- Add entity/topic extraction alongside sentiment.
- Backfill history and add week-over-week trend metrics.
- Swap the committed-file pattern for a cloud object store (S3) + MotherDuck.
- Add Slack/email alerts when daily sentiment crosses a threshold.

---

## License

MIT — free to use and adapt.
