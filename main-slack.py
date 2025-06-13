import os
import logging
import tweepy
import requests
import re
import json
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from requests.exceptions import ConnectionError

# Load .env file
load_dotenv()

# Twitter API credentials
BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")

# Slack Webhook URL
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

# Load keyword config from external JSON file
with open("keywords.json") as f:
    keyword_config = json.load(f)

PRIMARY_KEYWORDS = keyword_config.get("primary_keywords", [])
PRODUCT = keyword_config.get("product_keywords", [])
SERVICE= keyword_config.get("service_keywords", [])

# Setup logging
logging.basicConfig(level=logging.INFO)

# Initialize Tweepy client
client = tweepy.Client(bearer_token=BEARER_TOKEN)


def send_to_slack_blockkit(blocks, thread_ts=None):
    payload = {"blocks": blocks}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    response = requests.post(SLACK_WEBHOOK_URL, json=payload)
    if response.status_code != 200:
        logging.error(f"Slack webhook error: {response.status_code} {response.text}")
    return None


def strip_links(text):
    return re.sub(r'https?://\S+', '', text)


def format_tweet_blockkit(tweet, user):
    tweet_text = getattr(tweet, "note_tweet", {}).get("full_text", tweet.text)
    clean_text = strip_links(tweet_text).strip()
    tweet_url = f"https://twitter.com/{user.username}/status/{tweet.id}"

    return [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*New tweet by @{user.username}* _(Followers: {user.public_metrics['followers_count']})_\n\n{clean_text}"
            }
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"👍 {tweet.public_metrics.get('like_count', 0)}   🔁 {tweet.public_metrics.get('retweet_count', 0)}   💬 {tweet.public_metrics.get('reply_count', 0)}   👀 {tweet.public_metrics.get('impression_count', 0)}"}
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"<https://twitter.com/{user.username}/status/{tweet.id}|🔗 View on Twitter>"
            }
        }
    ]


def search_and_notify():
    one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat()
    keyword_clauses = [f'"{kw}"' for kw in PRIMARY_KEYWORDS + PRODUCT]
    query = f"({' OR '.join(keyword_clauses)}) -is:retweet -is:reply lang:en"

    try:
        tweets = client.search_recent_tweets(
            query=query,
            max_results=100,
            start_time=one_day_ago,
            tweet_fields=["created_at", "author_id", "text", "note_tweet", "public_metrics"],
            user_fields=["username", "public_metrics"],
            expansions=["author_id"]
        )
    except ConnectionError as e:
        logging.error(f"Twitter API connection error: {e}")
        return
    except Exception as e:
        logging.error(f"Unexpected error during Twitter API call: {e}")
        return

    if tweets.data:
        user_dict = {user.id: user for user in tweets.includes["users"]}

        # Sort tweets by impression count (if available)
        sorted_tweets = sorted(
            tweets.data,
            key=lambda t: t.public_metrics.get("impression_count", 0),
            reverse=True
        )[:100]  # Limit to top 100 by impressions

        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        header_block = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*🕓 Keyword Tracker - Tweets from: {timestamp}*"}
            },
            {"type": "divider"}
        ]
        thread_ts = send_to_slack_blockkit(header_block)

        for tweet in sorted_tweets:
            user = user_dict.get(tweet.author_id)
            if user:
                tweet_text = getattr(tweet, "note_tweet", {}).get("full_text", tweet.text)
                tweet_url = f"https://twitter.com/{user.username}/status/{tweet.id}"

                # Skip tweets containing spam tokens
                if any(token.lower() in tweet_text.lower() for token in SERVICE):
                    logging.info(f"Tweet skipped due to spam token: {tweet_url}")
                    continue

                # Skip tweets containing "ransomware"
                if "ransomware" in tweet_text.lower():
                    logging.info(f"Tweet skipped due to containing 'ransomware': {tweet_url}")
                    continue

                # Skip users with fewer than 20 followers
                followers = user.public_metrics.get("followers_count", 0)
                if followers < 20:
                    logging.info(f"Tweet skipped due to low follower count ({followers}): {tweet_url}")
                    continue

                if any(kw in tweet_text for kw in PRIMARY_KEYWORDS + PRODUCT):
                    blocks = format_tweet_blockkit(tweet, user)
                    logging.info(f"Tweet matched: {tweet.text}")
                    send_to_slack_blockkit(blocks, thread_ts=thread_ts)
    else:
        logging.info("No tweets found in the past 24 hours.")


if __name__ == "__main__":
    search_and_notify()
