{% snapshot snap_restaurants %}
{{ config(
    unique_key='restaurant_id',
    strategy='check',
    check_cols=['restaurant_name', 'rating', 'rating_count', 'cost_for_two', 'cuisine']
) }}
-- SCD2 history: every time a restaurant's rating/price/cuisine changes, dbt keeps the old row
-- (dbt_valid_from / dbt_valid_to) and inserts the new version.
select * from {{ ref('stg_restaurants') }}
{% endsnapshot %}
