-- Cleaned, typed view over the raw headlines.
-- One row per headline; downstream marts build on this.

with source as (
    select * from {{ source('raw', 'raw_headlines') }}
)

select
    id                                  as headline_id,
    nullif(trim(source), '')            as source_name,
    nullif(trim(author), '')            as author,
    trim(title)                         as title,
    nullif(trim(description), '')       as description,
    url,
    published_at,
    cast(published_at as date)          as published_date,
    sentiment,
    summary,
    fetched_at
from source
where title is not null
  and sentiment is not null
