"""Sample 3 events from Event Hubs and print body/score fields."""
from __future__ import annotations
import json
import threading
import time

from azure.eventhub import EventHubConsumerClient
from azure.identity import DefaultAzureCredential

cred = DefaultAzureCredential()
client = EventHubConsumerClient(
    fully_qualified_namespace="evhns-narrative-tinkerhub.servicebus.windows.net",
    eventhub_name="reddit-raw-events",
    consumer_group="$Default",
    credential=cred,
)
samples: list[dict] = []

def on_event(ctx, event):
    if event and len(samples) < 5:
        samples.append(json.loads(event.body_as_str()))

t = threading.Thread(
    target=client.receive,
    kwargs={"on_event": on_event, "starting_position": "-1"},
    daemon=True,
)
t.start()
time.sleep(12)
client.close()
t.join(timeout=5)

print(f"Sampled {len(samples)} events\n")
for s in samples:
    body = s.get("body", "")
    print(f"post_id={s.get('post_id')} subreddit={s.get('subreddit')} score={s.get('score')} body_len={len(body)}")
    print(f"  body[:120]: {body[:120]!r}")
    print()
