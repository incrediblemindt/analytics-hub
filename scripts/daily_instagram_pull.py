"""
daily_instagram_pull.py

Pulls:
    - Account information
    - Media metadata
    - Media insights

Loads:
    - instagram_dim_post
    - instagram_fact_post_daily
    - instagram_fact_account_daily
"""

import os
import requests
from datetime import datetime, date
from dotenv import load_dotenv
from google.cloud import bigquery
from google.auth import default

# ---------------------------------------------------
# Config
# ---------------------------------------------------

load_dotenv()

ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
IG_USER_ID = os.getenv("INSTAGRAM_USER_ID")

GRAPH_VERSION = "v24.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"

PROJECT_ID = "the-incredible-team"
DATASET_CORE = "social"

bq_client = bigquery.Client(
    project=PROJECT_ID,
    location="us-central1"
)

credentials, project = default()

print("ADC Project:", project)
print("ADC Credentials:", type(credentials))
print("BQ Client Project:", bq_client.project)

# ---------------------------------------------------
# BigQuery Helpers
# ---------------------------------------------------



def insert_rows(table_name, rows):

    if not rows:
        print(f"No rows for {table_name}")
        return

    table_id = f"{PROJECT_ID}.{DATASET_CORE}.{table_name}"

    job = bq_client.load_table_from_json(
        rows,
        table_id
    )

    job.result()

    print(f"Loaded {len(rows)} rows into {table_name}")

    







def execute_query(sql):






    job = bq_client.query(sql)



    job.result()









# ---------------------------------------------------


# Graph API Helper


# ---------------------------------------------------




def graph_get(endpoint, params=None):

    url = f"{BASE_URL}/{endpoint.lstrip('/')}"

    if params is None:
        params = {}

    params["access_token"] = ACCESS_TOKEN

    response = requests.get(
        url,
        params=params,
        timeout=30
    )

    if not response.ok:
        print("\n--- INSTAGRAM API ERROR ---")
        print("Status:", response.status_code)
        print("URL:", response.url)
        print("Response:", response.text)
        print("---------------------------\n")

        response.raise_for_status()

    return response.json()








# ---------------------------------------------------


# Instagram API


# ---------------------------------------------------





def get_account_info():






    fields = ",".join([

    

        "username",

    

        "followers_count",

    

        "follows_count",

    

        "media_count"

    

    ])






    return graph_get(

    

        IG_USER_ID,

    

        {

        

            "fields": fields

        

        }

    

    )









def get_media():






    fields = ",".join([

    

        "id",

    

        "caption",

    

        "media_type",

    

        "media_product_type",

    

        "permalink",

    

        "timestamp"

    

    ])






    endpoint = f"{IG_USER_ID}/media"






    media = []






    while True:

    




        response = graph_get(

        

            endpoint,

        

            {

            

                "fields": fields

            

            }

        

        )

    




        media.extend(

        

            response.get("data", [])

        

        )

    




        paging = response.get("paging", {})

    




        if "next" not in paging:

        

            break

    




        endpoint = paging["next"].replace(

        

            f"{BASE_URL}/",

        

            ""

        

        )

    




    return media









def get_media_insights(media_id):






    metrics = ",".join([

    

        "views",

    

        "reach",

    

        "likes",

    

        "comments",

    

        "shares",

    

        "saved"

    

    ])






    try:

    




        return graph_get(

        

            f"{media_id}/insights",

        

            {

            

                "metric": metrics

            

            }

        

        )

    




    except requests.HTTPError as e:

    




        print(f"Could not retrieve insights for {media_id}")

    

        print(e)

    




        return None

    







# ---------------------------------------------------


# Helpers


# ---------------------------------------------------





def flatten_insights(insights):






    metrics = {}






    if not insights:

    

        return metrics

    




    for metric in insights.get("data", []):

    




        metrics[metric["name"]] = metric["values"][0]["value"]

    




    return metrics


# ---------------------------------------------------
# Main
# ---------------------------------------------------

def main():

    snapshot_date = str(date.today())
    extracted_at = datetime.utcnow().isoformat()

    print("Retrieving account...")
    account = get_account_info()

    print("Retrieving media...")
    media = get_media()

    print(f"Found {len(media)} posts")

    # ---------------------------------------------------
    # Build Account Daily Fact
    # ---------------------------------------------------

    account_rows = [{
        "snapshot_date": snapshot_date,
        "ig_user_id": IG_USER_ID,
        "username": account.get("username"),
        "followers": account.get("followers_count"),
        "following_count": account.get("follows_count"),
        "media_count": account.get("media_count"),
        "extracted_at": extracted_at
    }]

    # ---------------------------------------------------
    # Build Dimension + Daily Fact
    # ---------------------------------------------------

    dim_rows = []
    fact_rows = []

    for post in media:

        print(f"Processing {post['id']}...")

        insights = flatten_insights(
            get_media_insights(post["id"])
        )
        post_timestamp = None

        if post.get("timestamp"):
            post_timestamp = (
                datetime.strptime(
                    post["timestamp"],
                    "%Y-%m-%dT%H:%M:%S%z"
                )
                .isoformat(sep=" ")
            )
        # ----------------------------
        # Dimension
        # ----------------------------

        dim_rows.append({

            "post_id": post["id"],
            "ig_user_id": IG_USER_ID,

            "caption": post.get("caption"),
            "media_type": post.get("media_type"),
            "media_product_type": post.get("media_product_type"),
            "permalink": post.get("permalink"),

            "post_timestamp": post_timestamp,

            "inserted_at": extracted_at,
            "updated_at": extracted_at

        })

        # ----------------------------
        # Daily Fact
        # ----------------------------

        fact_rows.append({

            "snapshot_date": snapshot_date,

            "post_id": post["id"],

            "views": insights.get("views", 0),
            "reach": insights.get("reach", 0),
            "likes": insights.get("likes", 0),
            "comments": insights.get("comments", 0),
            "shares": insights.get("shares", 0),
            "saved": insights.get("saved", 0),

            "extracted_at": extracted_at

        })

    # ---------------------------------------------------
    # Remove Today's Facts
    # ---------------------------------------------------

    print("Cleaning today's snapshots...")

    execute_query(f"""
    DELETE FROM `{PROJECT_ID}.{DATASET_CORE}.instagram_fact_account_daily`
    WHERE snapshot_date = DATE('{snapshot_date}')
    """)

    execute_query(f"""
    DELETE FROM `{PROJECT_ID}.{DATASET_CORE}.instagram_fact_post_daily`
    WHERE snapshot_date = DATE('{snapshot_date}')
    """)

    # ---------------------------------------------------
    # Refresh Dimension
    # ---------------------------------------------------

    print("Refreshing dimension...")

    execute_query(f"""
    TRUNCATE TABLE `{PROJECT_ID}.{DATASET_CORE}.instagram_dim_post`
    """)

    # ---------------------------------------------------
    # Load BigQuery
    # ---------------------------------------------------

    print("Loading account table...")
    insert_rows(
        "instagram_fact_account_daily",
        account_rows
    )

    print("Loading dimension...")
    insert_rows(
        "instagram_dim_post",
        dim_rows
    )

    print("Loading daily facts...")
    insert_rows(
        "instagram_fact_post_daily",
        fact_rows
    )

    print()
    print("=" * 60)
    print("Instagram load complete.")
    print(f"Posts Loaded: {len(dim_rows)}")
    print("=" * 60)


if __name__ == "__main__":
    main()