import os
import numpy as np
import pandas as pd
import streamlit as st
import snowflake.connector
from openai import OpenAI
import json
import re
from dotenv import load_dotenv

load_dotenv()

MODEL = "gpt-4o-mini"

FORBIDDEN_WORDS = ['drop', 'delete', 'truncate', 'alter', 'update', 'insert', 'create', 'replace', 'grant', 'revoke']

EXAMPLE_QUESTIONS = [
    "Top 10 cities by GMV",
    "Which cuisine has the most orders?",
    "Average delivery time by city, worst first",
    "Cancel rate by payment method"
]

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SCHEMA = """
Tables available (Snowflake). Use bare table names, no database or schema prefix.

FCT_ORDERS(order_id, order_timestamp, order_date, customer_id, restaurant_id, city, cuisine,
           payment_method, order_status, is_delivered, items_count, sales_qty, subtotal,
           discount, delivery_fee, gst, sales_amount, customer_rating, delivery_time_min)
FACT_ORDER_ITEMS(order_item_id, order_id, restaurant_id, f_id, order_ts, order_date, city,
                 price, quantity, line_amount)
DIM_RESTAURANTS(restaurant_id, restaurant_name, city, cuisine, rating, rating_count, cost_for_two)
DIM_CUSTOMER(customer_id, customer_name, email, age, age_segment, gender, marital_status,
             occupation, income_band, education, family_size)
DIM_FOOD(f_id, food_name, veg_or_non_veg)
DIM_DATE(date_day, year, month, month_name, day_name, is_weekend)
MART_DAILY_CITY_REVENUE(order_date, city, orders, delivered_orders, cancel_rate, gmv, aov)
MART_RESTAURANT_PERFORMANCE(restaurant_id, restaurant_name, city, cuisine,
                            orders, revenue, avg_customer_rating, avg_delivery_min)
MART_DELIVERY_SLA(city, order_hour, delivered_orders, p50, p90)   -- p50/p90 = delivery minutes

Notes: order_status is one of 'Delivered', 'Cancelled', 'Refunded'. gmv means delivered revenue.
Prefer the MART_ tables when they fit the question.
"""
 
SYSTEM_PROMPT = f"""
You are a Snowflake SQL expert. Write ONE SELECT query that answers the question.
 
Rules:
- SELECT queries only, never modify data.
- Use bare table names (FCT_ORDERS, not ZOMATO.MARTS.FCT_ORDERS).
- Add a LIMIT of 100 or less, unless the question asks for a single total.
- Reply as JSON in this exact format: {{"sql": "your query here"}}
 
{SCHEMA}
"""
 


@st.cache_resource
def get_connection():
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "ZOMATO_WH"),
        database=os.getenv("SNOWFLAKE_DATABASE", "ZOMATO"),
        schema="MARTS",
        role = "DBT_ROLE"
    )


def generate_sql(question):
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question}
        ]
    )
    answer = response.choices[0].message.content
    sql = json.loads(answer)["sql"]

    sql = sql.replace("ZOMATO.MARTS.", "").replace("ZOMATO.", "")
    return sql.strip().rstrip(";")


def is_safe(sql):
    lowered = sql.lower()

    if not lowered.startswith("select") and not lowered.startswith("with"):
        return False

    if ";" in lowered:          # one statement only
        return False
    for word in FORBIDDEN_WORDS:
        if re.search(rf"\b{word}\b", lowered):
            return False

    return True

def run_query(sql):
    conn = get_connection()
    cursor = conn.cursor()
    return cursor.execute(sql).fetch_pandas_all()


st.title("Chat with your Zomato Data")
st.caption(f"Ask in English, {MODEL} writes the SQL, Snowflake runs it")

with st.sidebar:
    st.header("Example Questions")
    for q in EXAMPLE_QUESTIONS:
        st.markdown(f" - {q}")

question = st.text_input("Enter your question here", 
                         placeholder="e.g. Top 10 restaurants by revenue in Bangalore")


if question:
    sql = generate_sql(question)
    st.code(sql, language="sql")

    if not is_safe(sql):
        st.error("The generated SQL is not safe to run. Please modify your question.")

    else:
        try:
            df = run_query(sql)
            st.success(f"{len(df)} rows returned")
            st.dataframe(df, hide_index=True)

            if len(df.columns) == 2 and pd.api.types.is_numeric_dtype(df.iloc[:, 1]):
                st.bar_chart(df, x=df.columns[0], y=df.columns[1])

        except Exception as e:
            st.error(f"Error running query: {e}")
