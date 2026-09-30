# RUNBOOK — build the Zomato pipeline step by step

Follow the phases in order. Each phase ends with a **✅ check**. Don't move on until it passes.
Commands are PowerShell (Windows). Run them from the project root unless the step says otherwise.

---

## Phase 0 · Accounts & tools

| Need | Notes |
|---|---|
| **AWS account** | S3 + IAM only. 2.3 GB in S3 costs a few cents a month. |
| **Snowflake trial** | Sign up at signup.snowflake.com → **Enterprise**, cloud **AWS**, and pick the **same region** you'll use for the S3 bucket (e.g. `us-east-1`). You get $400 of free credits, and the whole project uses a small fraction of that. |
| **OpenAI API key** | platform.openai.com → API keys. Needs a little prepaid credit ($5 is plenty; `SAMPLE_N=5` costs fractions of a cent per run). |
| **Docker Desktop** | Already installed ✔. Give it **≥ 4 GB RAM** (Settings → Resources). |
| **Python 3.12** | Already installed ✔. Use 3.12 specifically: dbt 1.8 doesn't install on your default Python 3.14. |

Create the local Python environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

✅ `dbt --version` prints `1.8.x` with the `snowflake` plugin.

---

## Phase 1 · Get the dataset

Download the CSVs from the tutorial's Google Drive folder (linked in README.md) and put them in `data/` (already git-ignored):

```
data/restaurant.csv   data/users.csv   data/food.csv   data/menu.csv
data/orders.csv       data/order_items.csv            data/reviews.csv
```

(The exact file names don't matter. What matters is which S3 folder each file goes into in Phase 2.)

✅ Seven CSVs, ~2.3 GB total.

---

## Phase 2 · Amazon S3 (the lake)

1. AWS Console → **S3** → *Create bucket*. Name it something like `zomato-dl-<yourname>` and use the **same region as your Snowflake account**. Leave all other settings at their defaults (Block Public Access ON).
2. Inside the bucket create the folder `raw/` and, inside it, one folder per table:
   `restaurants/ users/ food/ menu/ orders/ order_items/ reviews/`
3. Upload each CSV into its folder (e.g. `restaurant.csv` → `raw/restaurants/`). The console handles the big files fine; `order_items` takes a while.

✅ `s3://<bucket>/raw/` shows 7 folders, each with exactly one CSV.

---

## Phase 3 · Keyless S3 ↔ Snowflake link

This is the fiddly part. Do it in exactly this order.

**A. IAM policy** (AWS Console → IAM → Policies → *Create policy* → JSON):
paste `aws/iam/s3-read-policy.json` with `<BUCKET>` replaced → name it `zomato-s3-read`.

**B. IAM role** (IAM → Roles → *Create role* → **Custom trust policy**):
paste `aws/iam/snowflake-role-trust-policy-initial.json` with `<ACCOUNT_ID>` = your 12-digit AWS account id →
attach `zomato-s3-read` → name it `snowflake-zomato-role`. Copy its **ARN**.

**C. Snowflake** (Snowsight → *Projects → Worksheets* → new SQL worksheet), run:
- `snowflake/01_setup.sql` (whole file) → warehouse, database, schemas, `DBT_ROLE`
- `snowflake/02_storage_integration.sql` with `<ROLE_ARN>` and `<BUCKET>` filled in

From the `DESC INTEGRATION` output, copy **STORAGE_AWS_IAM_USER_ARN** and **STORAGE_AWS_EXTERNAL_ID**.

**D. Back in AWS** → the role → *Trust relationships* → *Edit*: paste
`aws/iam/snowflake-role-trust-policy-final.json` with those two values filled in.

> ⚠️ After step D, **never re-run `CREATE OR REPLACE STORAGE INTEGRATION`**. It generates a new external ID and breaks the trust. If you must re-run it, repeat step D.

---

## Phase 4 · Load RAW (Bronze)

In Snowsight, run in order (replace `<BUCKET>` in 03):

1. `snowflake/03_stage_and_formats.sql`: its `LIST @...` should show your 7 files
2. `snowflake/04_raw_tables.sql`
3. `snowflake/05_copy_into.sql`

✅ The final count query shows orders = 10,000,000, order_items ≈ 23M, reviews = 300K, restaurants ≈ 148K.

If `LIST` fails with *access denied*, the trust policy is wrong (Phase 3 D). Re-check the ARN and external ID.

---

## Phase 5 · dbt (Silver + Gold)

Set credentials in your shell. dbt reads them via `zomato/profiles.yml`:

```powershell
$env:SNOWFLAKE_ACCOUNT  = "ORGNAME-ACCOUNTNAME"   # Snowsight → your name → Account → View account details
$env:SNOWFLAKE_USER     = "your_user"
$env:SNOWFLAKE_PASSWORD = "your_password"
cd zomato
dbt debug                          # must say "All checks passed!"
dbt build --exclude tag:ai         # staging views → dims/facts/marts → snapshot → tests
```

First build: a few minutes on an XSMALL warehouse (fact_order_items is 23M rows).
Run `dbt build --exclude tag:ai` a second time. It's much faster because the facts are **incremental** (MERGE of new rows only).

✅ Every model and test is PASS. Snowsight shows tables in `ZOMATO.STAGING`, `ZOMATO.MARTS` and `ZOMATO.SNAPSHOTS`.

If you see an MFA / password error: Snowflake now pushes MFA on password logins. Create a service user for dbt/Airflow (`CREATE USER dbt_svc PASSWORD='...' DEFAULT_ROLE=DBT_ROLE TYPE=LEGACY_SERVICE; GRANT ROLE DBT_ROLE TO USER dbt_svc;`) and use that.

---

## Phase 6 · AI layer

```powershell
copy ai\example.env ai\.env         # fill in Snowflake + OPENAI_API_KEY
python ai\enrich_reviews.py         # classifies SAMPLE_N new reviews → ZOMATO.AI.REVIEW_ENRICHED
cd zomato; dbt build --select tag:ai; cd ..   # builds mart_review_insights
```

Run `enrich_reviews.py` a few times, or raise `SAMPLE_N` to e.g. 200, so the insights mart has something to show. It's idempotent: already-enriched reviews are never sent again.

```powershell
streamlit run ai\rag_chat.py        # "What do people complain about in delivery?"
streamlit run ai\text_to_sql.py     # "Top 10 cities by GMV"
```

The first RAG launch samples 500 reviews, embeds them and caches the result in `ai/review_embeddings.parquet`. Delete that file to re-sample.

✅ Both apps answer questions. RAG shows its source reviews, and text-to-SQL shows the SQL plus a result table.

---

## Phase 7 · Orchestrate with Airflow 3

```powershell
cd airflow
copy example.env .env               # fill SNOWFLAKE_*, OPENAI_API_KEY, SAMPLE_N
docker compose build
docker compose up -d
```

Open http://localhost:8080 → login **admin / admin** → un-pause **zomato_batch** → *Trigger*.

✅ All 4 tasks go green: `reload_raw → dbt_build_core → enrich_reviews → dbt_build_ai`.
(`reload_raw` is quick on re-runs because Snowflake's load history skips files it already loaded.)

Stop it with `docker compose down` (add `-v` to also wipe Airflow's metadata DB).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `reload_raw` fails with *insufficient privileges on stage* | Re-run the two `GRANT USAGE` lines at the end of `03_stage_and_formats.sql`. |
| Airflow tasks die with *state mismatch* | Keep `AIRFLOW__CORE__EXECUTION_API_SERVER_URL` pointing at `http://apiserver:8080/execution/` (already set). |
| dbt `relationships` test fails on `fct_orders.customer_id` | Some orders reference users that were rejected by `ON_ERROR='CONTINUE'` in users. Check `SELECT * FROM TABLE(VALIDATE(RAW.users, JOB_ID=>'_last'))`. |
| Credits disappearing | `ALTER WAREHOUSE ZOMATO_WH SUSPEND;`. Auto-suspend is already 60 s. |
