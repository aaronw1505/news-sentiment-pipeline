-- Sentiment breakdown per news source, ranked by volume.

with headlines as (
    select * from {{ ref('stg_headlines') }}
    where source_name is not null
)

select
    source_name,
    count(*)                                                        as total_headlines,
    count(*) filter (where sentiment = 'Positive')                  as positive_count,
    count(*) filter (where sentiment = 'Neutral')                   as neutral_count,
    count(*) filter (where sentiment = 'Negative')                  as negative_count,
    round(
        avg(case sentiment
                when 'Positive' then 1
                when 'Negative' then -1
                else 0
            end),
        3
    )                                                               as avg_sentiment_score
from headlines
group by source_name
order by total_headlines desc
