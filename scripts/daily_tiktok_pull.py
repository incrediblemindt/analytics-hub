import requests
from datetime import datetime, date
from google.cloud import bigquery
from google.auth import default

# ----------------------------
# Config
# ----------------------------

TIKTOK_API = "https://open.tiktokapis.com/v2"

CLIENT_KEY = "sbawb5kwd419ktzynf"
CLIENT_SECRET = "1C1NFiQrjGBoQgHUpceZgPW87R3ZRq5A"
REFRESH_TOKEN = "rft.mMl1Pn8txWi6yg2mFloDDDwU44Lss1hlYIkvrUZjhQO6lnkh3vCFg9Yue2Pw!6422.u1"

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

# ----------------------------
# BigQuery Helpers
# ----------------------------

def insert_rows(table_name, rows):
    table_id = f"{PROJECT_ID}.{DATASET_CORE}.{table_name}"

    errors = bq_client.insert_rows_json(table_id, rows)

    if errors:
        print(f"Errors inserting into {table_name}:")
        print(errors)
    else:
        print(f"Inserted {len(rows)} rows into {table_name}")


def execute_query(sql):
    job = bq_client.query(sql)
    job.result()

# ----------------------------
# TikTok Auth
# ----------------------------

def refresh_access_token():

    url = f"{TIKTOK_API}/oauth/token/"

    payload = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN
    }

    r = requests.post(
        url,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )

    r.raise_for_status()

    return r.json()["access_token"]

# ----------------------------
# API Calls
# ----------------------------

def get_user_info(token):

    url = f"{TIKTOK_API}/user/info/"

    headers = {
        "Authorization": f"Bearer {token}"
    }

    params = {
        "fields": "open_id,display_name,follower_count,following_count,likes_count,video_count"
    }

    return requests.get(
        url,
        headers=headers,
        params=params
    ).json()


def list_videos(token):

    url = f"{TIKTOK_API}/video/list/"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    params = {
        "fields": "id,create_time,video_description,duration,share_url,view_count,like_count,comment_count,share_count"
    }

    all_videos = []

    cursor = None
    has_more = True

    while has_more:

        body = {
            "max_count": 20
        }

        if cursor:
            body["cursor"] = cursor

        r = requests.post(
            url,
            headers=headers,
            params=params,
            json=body
        )

        r.raise_for_status()

        data = r.json()["data"]

        all_videos.extend(
            data.get("videos", [])
        )

        has_more = data.get("has_more", False)
        cursor = data.get("cursor")

    return all_videos

# ----------------------------
# Main
# ----------------------------

if __name__ == "__main__":

    snapshot_date = str(date.today())
    extracted_at = datetime.utcnow().isoformat()

    token = refresh_access_token()

    user_resp = get_user_info(token)
    user = user_resp["data"]["user"]

    open_id = user["open_id"]

    videos = list_videos(token)

    print(f"Found {len(videos)} videos")

    # ----------------------------
    # Build Rows
    # ----------------------------

    account_rows = [{
        "snapshot_date": snapshot_date,
        "open_id": open_id,
        "follower_count": user.get("follower_count"),
        "following_count": user.get("following_count"),
        "likes_count": user.get("likes_count"),
        "video_count": user.get("video_count"),
        "extracted_at": extracted_at
    }]

    dim_rows = []
    fact_rows = []

    for v in videos:

        video_id = v["id"]

        dim_rows.append({

            "video_id": video_id,
            "open_id": open_id,

            "create_time":
                datetime.utcfromtimestamp(
                    v["create_time"]
                ).isoformat(),

            "duration": v.get("duration"),
            "share_url": v.get("share_url"),
            "video_description": v.get("video_description"),

            "first_seen_date": snapshot_date,
            "last_seen_date": snapshot_date,

            "inserted_at": extracted_at,
            "updated_at": extracted_at

        })

        fact_rows.append({

            "snapshot_date": snapshot_date,

            "video_id": video_id,
            "open_id": open_id,

            "view_count": v.get("view_count"),
            "like_count": v.get("like_count"),
            "comment_count": v.get("comment_count"),
            "share_count": v.get("share_count"),

            "extracted_at": extracted_at

        })

    # ----------------------------
    # Remove today's facts
    # ----------------------------

    execute_query(f"""
    DELETE FROM `{PROJECT_ID}.{DATASET_CORE}.tiktok_fact_account_daily`
    WHERE snapshot_date = DATE('{snapshot_date}')
    """)

    execute_query(f"""
    DELETE FROM `{PROJECT_ID}.{DATASET_CORE}.tiktok_fact_video_daily`
    WHERE snapshot_date = DATE('{snapshot_date}')
    """)

    # ----------------------------
    # Refresh Dimension
    # ----------------------------

    execute_query(f"""
    TRUNCATE TABLE `{PROJECT_ID}.{DATASET_CORE}.tiktok_dim_video`
    """)

    # ----------------------------
    # Load BigQuery
    # ----------------------------

    insert_rows("tiktok_fact_account_daily", account_rows)
    insert_rows("tiktok_dim_video", dim_rows)
    insert_rows("tiktok_fact_video_daily", fact_rows)

    print("TikTok structured load complete.")