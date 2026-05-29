-- Daily sentiment aggregation: counts per label + an average sentiment score.
-- score = mean of (Positive=+1, Neutral=0, Negative=-1), range [-1, +1].

with headlines as (
    select * from {{ ref('stg_headlines') }}
    where published_date is not null
)

select
    published_date,
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
group by published_date
order by published_date
