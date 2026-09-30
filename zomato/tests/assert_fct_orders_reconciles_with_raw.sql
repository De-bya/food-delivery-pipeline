-- Singular test: every RAW order must make it into fct_orders. Returns rows (= fails) on mismatch.
with raw_orders as (
    select count(*) as raw_count from {{ source('raw', 'orders') }}
),
fct as (
    select count(*) as fct_count from {{ ref('fct_orders') }}
)
select raw_count, fct_count
from raw_orders cross join fct
where raw_count <> fct_count
