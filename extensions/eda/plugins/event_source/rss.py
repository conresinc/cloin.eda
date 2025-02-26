import aiohttp
import asyncio
import feedparser
import logging
import json
import os
from typing import List, Dict, Optional, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOCUMENTATION = r'''
module: rss
short_description: Event-Driven Ansible source plugin for RSS feed events
description:
    - Poll multiple RSS feeds for new items
    - Only retrieves items that occurred after the script began executing
author: "Colin McNaughton @cloin - https://github.com/cloin"
options:
    feed_configs:
        description:
            - A list of dictionaries, each representing a configuration for an RSS feed.
            - Each dictionary must have a 'url' key containing the URL of the RSS feed.
            - Optionally, a 'search' key can be included to filter items based on a search string present in the item's summary.
            - Optionally, a 'content_tags' key can be provided to specify the dot-notation path for content tags within each feed item. If found, a new key "content_tags" is added to the dictionary.
            - Optionally, an 'interval' key can be provided to specify the polling interval in seconds for this specific feed. If not provided, the global 'interval' value is used.
            - Optionally, a 'name' key can be provided to specify the name of the feed. This name will be added as "feed_name" in each event.
        required: true
        type: list
        elements: dict
        example:
            - url: "http://example.com/rss1"
              search: "python"
              content_tags: "tags.label"
              interval: 30
              name: "Example RSS"
    interval:
        description:
            - The default interval, in seconds, at which the script polls the feeds. This value is used when an individual 'interval' is not specified in the 'feed_configs'.
        required: false
        default: 7200
    most_recent_item:
        description:
            - When True, the most recent item on the RSS feed will be used as the first event dictionary.
        required: false
        default: False
        type: bool
'''

EXAMPLES = r'''
- name: New Ansible blog post notifier
  hosts: localhost
  sources:
    - name: RSS feed items as events
      cloin.eda.rss:
        feed_configs:
          - name: Ansible blog
            url: https://www.ansible.com/blog/rss.xml
            search: ""
            content_tags: tags
            interval: 300
        interval: 7200
        most_recent_item: false
      filters:
        - ansible.eda.json_filter:
            exclude_keys:
              - summary

  rules:
    - name: New EDA blog
      condition: |
        event.id is defined and
        event.content_tags is search("Event-Driven Ansible",ignorecase=true)
      action:
        debug:

'''

def get_nested_value(dictionary: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if dictionary is None or not isinstance(dictionary, dict):
            return None
        dictionary = dictionary.get(key)
    return dictionary

async def fetch_rss_feed(session: aiohttp.ClientSession, feed_url: str) -> str:
    try:
        async with session.get(feed_url) as response:
            if response.status == 200:
                return await response.text()
            else:
                logger.error(f"Failed to fetch {feed_url}, status: {response.status}")
                return ""
    except Exception as e:
        logger.error(f"Error fetching {feed_url}: {e}")
        return ""

async def poll_feed(queue: asyncio.Queue, session: aiohttp.ClientSession, feed_config: Dict[str, Any], default_interval: int, most_recent_item: bool):
    feed_url = feed_config.get('url')
    search_string = feed_config.get('search')
    content_tags_path = feed_config.get('content_tags')
    content_tags_keys = content_tags_path.split('.') if content_tags_path else []
    interval = feed_config.get('interval') or default_interval
    feed_name = feed_config.get('name', '')
    last_updated = None
    first_poll = True

    while True:
        try:
            feed_data = await fetch_rss_feed(session, feed_url)
            if feed_data:
                feed = feedparser.parse(feed_data)

                if first_poll and most_recent_item and feed.entries:
                    entry = feed.entries[0]
                    if search_string is None or search_string in entry.summary:
                        content_tags = get_nested_value(entry, content_tags_keys) if content_tags_keys else None
                        if content_tags is not None:
                            entry['content_tags'] = content_tags
                        entry['feed_name'] = feed_name
                        await queue.put(entry)
                    first_poll = False
                elif last_updated:
                    for entry in feed.entries:
                        if entry.updated_parsed > last_updated and (search_string is None or search_string in entry.summary):
                            content_tags = get_nested_value(entry, content_tags_keys) if content_tags_keys else None
                            if content_tags is not None:
                                entry['content_tags'] = content_tags
                            entry['feed_name'] = feed_name
                            await queue.put(entry)

                last_updated = feed.feed.updated_parsed if hasattr(feed.feed, 'updated_parsed') else None
            else:
                logger.error(f"No data fetched for {feed_url}")
        except Exception as e:
            logger.error(f"Error polling {feed_url}: {e}")
        await asyncio.sleep(interval)

async def main(queue: asyncio.Queue, args: Dict[str, Any]):
    default_interval = int(args.get("interval", 7200))
    feed_configs = args.get("feed_configs", [])
    most_recent_item = args.get("most_recent_item", False)

    async with aiohttp.ClientSession() as session:
        tasks = [poll_feed(queue, session, config, default_interval, most_recent_item) for config in feed_configs]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    FEED_CONFIGS = os.getenv("FEED_CONFIGS", '[{"url": "http://example.com/rss"}]')
    INTERVAL = int(os.getenv("INTERVAL", "7200"))
    MOST_RECENT_ITEM = os.getenv("MOST_RECENT_ITEM", "False").lower() == "true"

    class MockQueue:
        async def put(self, event):
            print(event)

    args = {
        "feed_configs": json.loads(FEED_CONFIGS),
        "interval": INTERVAL,
        "most_recent_item": MOST_RECENT_ITEM,
    }
    asyncio.run(main(MockQueue(), args))
