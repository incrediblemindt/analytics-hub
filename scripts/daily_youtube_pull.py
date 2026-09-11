#########################################################
# IMPORTS
#########################################################

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from itertools import islice
from typing import Dict, Iterable, List

from google.cloud import bigquery
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


#########################################################
# CONFIG
#########################################################

@dataclass(frozen=True)
class Config:

    project_id: str
    dataset: str

    channel_id: str

    token_file: str

    scopes: List[str]


CONFIG = Config(

    project_id="the-incredible-team",

    dataset="social",

    channel_id=os.getenv(
        "YOUTUBE_CHANNEL_ID",
        "UC7W3cJqkpxO7I0IdvSTq9xg"
    ),

    token_file=os.path.join(
        os.path.dirname(__file__),
        "../credentials/token.json"
    ),

    scopes=[
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]

)


#########################################################
# LOGGING
#########################################################

logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s | %(levelname)s | %(message)s"

)

logger = logging.getLogger(__name__)


#########################################################
# CLIENTS
#########################################################

if (
    os.getenv("YOUTUBE_CLIENT_ID")
    and os.getenv("YOUTUBE_CLIENT_SECRET")
    and os.getenv("YOUTUBE_REFRESH_TOKEN")
):
    logger.info("Using YouTube credentials from environment variables.")

    credentials = Credentials(
        token=None,
        refresh_token=os.getenv("YOUTUBE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("YOUTUBE_CLIENT_ID"),
        client_secret=os.getenv("YOUTUBE_CLIENT_SECRET"),
        scopes=CONFIG.scopes,
    )

else:
    logger.info("Using local YouTube token file.")

    credentials = Credentials.from_authorized_user_file(
        CONFIG.token_file,
        CONFIG.scopes
    )

youtube = build(

    "youtube",

    "v3",

    credentials=credentials,

    cache_discovery=False

)

youtube_analytics = build(

    "youtubeAnalytics",

    "v2",

    credentials=credentials,

    cache_discovery=False

)

bq_client = bigquery.Client(

    project=CONFIG.project_id

)


#########################################################
# CONSTANTS
#########################################################

TODAY = date.today()

NOW = datetime.now(timezone.utc).isoformat(sep=" ")


#########################################################
# UTILITIES
#########################################################

def chunked(iterable, size):

    """
    Yield lists of length size.
    """

    iterator = iter(iterable)

    while True:

        chunk = list(islice(iterator, size))

        if not chunk:
            break

        yield chunk


def parse_duration(duration):

    """
    Converts ISO8601 duration to seconds.

    Examples

    PT53S

    PT1M37S

    PT2H4M8S
    """

    match = re.match(

        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",

        duration

    )

    if not match:

        return 0

    hours = int(match.group(1) or 0)

    minutes = int(match.group(2) or 0)

    seconds = int(match.group(3) or 0)

    return hours * 3600 + minutes * 60 + seconds


def parse_timestamp(timestamp):

    """
    Converts YouTube timestamps into a BigQuery friendly string.
    """

    return datetime.strptime(

        timestamp,

        "%Y-%m-%dT%H:%M:%SZ"

    ).isoformat(sep=" ")


#########################################################
# BIGQUERY HELPERS
#########################################################

def load_table(rows, table_name):

    table = (

        f"{CONFIG.project_id}."

        f"{CONFIG.dataset}."

        f"{table_name}"

    )

    job = bq_client.load_table_from_json(

        rows,

        table

    )

    job.result()

    logger.info(

        "Loaded %s rows into %s",

        len(rows),

        table_name

    )


def execute_sql(sql):

    job = bq_client.query(sql)

    job.result()


def truncate_table(table_name):

    execute_sql(

        f"""

        TRUNCATE TABLE
        `{CONFIG.project_id}.{CONFIG.dataset}.{table_name}`

        """

    )


def delete_today(table_name):

    execute_sql(

        f"""

        DELETE FROM
        `{CONFIG.project_id}.{CONFIG.dataset}.{table_name}`

        WHERE snapshot_date = CURRENT_DATE()

        """

    )


#########################################################
# ERROR HANDLER
#########################################################

def api_call(request):

    """
    Wrapper for every Google API request.
    """

    try:

        return request.execute()

    except HttpError as e:

        logger.exception(e)

        raise
#########################################################
# CHANNEL
#########################################################

def fetch_channel():
    """
    Returns the authenticated channel.
    """

    response = api_call(

        youtube.channels().list(
            part="id,snippet,statistics,contentDetails",
            mine=True
        )

    )

    return response["items"][0]


#########################################################
# UPLOADS PLAYLIST
#########################################################

def fetch_uploads_playlist_id(channel):
    """
    Every YouTube channel has a hidden uploads playlist
    containing every uploaded video.
    """

    return channel["contentDetails"]["relatedPlaylists"]["uploads"]


#########################################################
# VIDEO IDS
#########################################################

def fetch_video_ids(uploads_playlist_id):
    """
    Returns every uploaded video's ID.
    """

    logger.info("Fetching uploaded videos...")

    video_ids = []

    next_page = None

    while True:

        response = api_call(

            youtube.playlistItems().list(

                part="contentDetails",

                playlistId=uploads_playlist_id,

                maxResults=50,

                pageToken=next_page

            )

        )

        for item in response["items"]:

            video_ids.append(

                item["contentDetails"]["videoId"]

            )

        next_page = response.get("nextPageToken")

        if not next_page:
            break

    logger.info(

        "Found %s uploaded videos.",

        len(video_ids)

    )

    return video_ids


#########################################################
# VIDEO DETAILS
#########################################################

def fetch_video_details(video_ids):
    """
    Downloads metadata for every video.
    """

    logger.info("Downloading video metadata...")

    videos = []

    for chunk in chunked(video_ids, 50):

        response = api_call(

            youtube.videos().list(

                part=(
                    "snippet,"
                    "contentDetails,"
                    "status,"
                    "statistics"
                ),

                id=",".join(chunk)

            )

        )

        videos.extend(response["items"])

    logger.info(

        "Downloaded metadata for %s videos.",

        len(videos)

    )

    return videos


#########################################################
# VIDEO ANALYTICS
#########################################################

def fetch_video_analytics():

    response = api_call(

        youtube_analytics.reports().query(

            ids="channel==MINE",

            startDate="2000-01-01",

            endDate=str(TODAY),

            dimensions="video",

            metrics="views",

            sort="-views",

            maxResults=200

        )

    )

    print(response)

    return {}

#########################################################
# DIMENSION ROWS
#########################################################

def build_dim_video_rows(videos):
    """
    Build youtube_dim_video rows.
    """

    rows = []

    for video in videos:

        snippet = video["snippet"]

        content = video["contentDetails"]

        status = video["status"]

        rows.append({

            "video_id": video["id"],

            "channel_id": CONFIG.channel_id,

            "title": snippet.get("title"),

            "description": snippet.get("description"),

            "published_at": parse_timestamp(
                snippet["publishedAt"]
            ),

            "duration_seconds": parse_duration(
                content["duration"]
            ),

            "privacy_status": status.get(
                "privacyStatus"
            ),

            "inserted_at": NOW,

            "updated_at": NOW

        })

    logger.info(
        "Built %s dimension rows.",
        len(rows)
    )

    return rows


#########################################################
# CHANNEL FACT ROWS
#########################################################

def build_channel_fact_rows(channel):
    """
    Build youtube_fact_channel_daily.
    """

    stats = channel["statistics"]

    row = {

        "snapshot_date": TODAY.isoformat(),

        "channel_id": channel["id"],

        "channel_name": channel["snippet"]["title"],

        "subscribers": int(
            stats.get("subscriberCount", 0)
        ),

        "total_views": int(
            stats.get("viewCount", 0)
        ),

        "video_count": int(
            stats.get("videoCount", 0)
        ),

        "extracted_at": NOW

    }

    return [row]


#########################################################
# VIDEO FACT ROWS
#########################################################

def build_video_fact_rows(
    videos,
    analytics
):
    """
    Build youtube_fact_video_daily.
    """

    rows = []

    for video in videos:

        video_id = video["id"]

        stats = video["statistics"]

        analytic = analytics.get(video_id, {})

        rows.append({

            "snapshot_date": TODAY.isoformat(),

            "video_id": video_id,

            "views": int(
                stats.get("viewCount", 0)
            ),

            "likes": int(
                stats.get("likeCount", 0)
            ),

            "comments": int(
                stats.get("commentCount", 0)
            ),

            "estimated_minutes_watched":

                int(

                    analytic.get(
                        "estimated_minutes_watched",
                        0
                    )

                ),

            "average_view_duration":

                float(

                    analytic.get(
                        "average_view_duration",
                        0
                    )

                ),

            "impressions":

                int(

                    analytic.get(
                        "impressions",
                        0
                    )

                ),

            "impression_ctr":

                float(

                    analytic.get(
                        "impression_ctr",
                        0
                    )

                ),

            "subscribers_gained":

                int(

                    analytic.get(
                        "subscribers_gained",
                        0
                    )

                ),

            "subscribers_lost":

                int(

                    analytic.get(
                        "subscribers_lost",
                        0
                    )

                ),

            "extracted_at": NOW

        })

    logger.info(

        "Built %s video fact rows.",

        len(rows)

    )

    return rows

#########################################################
# MAIN
#########################################################

def main():

    logger.info("Starting YouTube ETL...")

    #####################################################
    # FETCH DATA
    #####################################################

    channel = fetch_channel()

    uploads_playlist = fetch_uploads_playlist_id(
        channel
    )

    video_ids = fetch_video_ids(
        uploads_playlist
    )

    videos = fetch_video_details(
        video_ids
    )

    analytics = fetch_video_analytics()

    #####################################################
    # BUILD ROWS
    #####################################################

    dim_rows = build_dim_video_rows(
        videos
    )

    channel_rows = build_channel_fact_rows(
        channel
    )

    video_fact_rows = build_video_fact_rows(
        videos,
        analytics
    )

    #####################################################
    # CLEAN TABLES
    #####################################################

    logger.info("Cleaning tables...")

    truncate_table(
        "youtube_dim_video"
    )

    delete_today(
        "youtube_fact_video_daily"
    )

    delete_today(
        "youtube_fact_channel_daily"
    )

    #####################################################
    # LOAD TABLES
    #####################################################

    logger.info("Loading youtube_dim_video...")

    load_table(
        dim_rows,
        "youtube_dim_video"
    )

    logger.info("Loading youtube_fact_video_daily...")

    load_table(
        video_fact_rows,
        "youtube_fact_video_daily"
    )

    logger.info("Loading youtube_fact_channel_daily...")

    load_table(
        channel_rows,
        "youtube_fact_channel_daily"
    )

    #####################################################
    # DONE
    #####################################################

    logger.info("----------------------------------")
    logger.info("YouTube ETL Complete")
    logger.info("----------------------------------")

    logger.info(
        "Videos Processed: %s",
        len(video_ids)
    )

    logger.info(
        "Dimension Rows: %s",
        len(dim_rows)
    )

    logger.info(
        "Video Fact Rows: %s",
        len(video_fact_rows)
    )

    logger.info(
        "Channel Fact Rows: %s",
        len(channel_rows)
    )


#########################################################
# ENTRYPOINT
#########################################################

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        logger.exception(
            "ETL Failed"
        )

        raise