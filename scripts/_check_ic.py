"""Quick check: count ic_snapshots docs and show recent scorer logs."""
import os, json, subprocess, sys

key = subprocess.check_output(
    "az cosmosdb keys list -g options-rg -n cosmos-nr-tinkerhub --query primaryMasterKey -o tsv",
    shell=True, text=True
).strip()

from azure.cosmos import CosmosClient
c = CosmosClient("https://cosmos-nr-tinkerhub.documents.azure.com:443/", {"masterKey": key})
db = c.get_database_client("narrative")
ic = db.get_container_client("ic_snapshots")

count = list(ic.query_items("SELECT VALUE COUNT(1) FROM c", enable_cross_partition_query=True))
print(f"ic_snapshots total docs: {count}")

if count and count[0] > 0:
    sample = list(ic.query_items(
        "SELECT TOP 5 c.ticker, c.snapshot_date, c.acs, c.is_complete FROM c ORDER BY c.snapshot_date DESC",
        enable_cross_partition_query=True
    ))
    print("Most recent snapshots:")
    print(json.dumps(sample, indent=2, default=str))
else:
    print("Container is empty — experiment has NOT started yet.")
    print("The scorer job is running every 20 min, but the deployed image may")
    print("not include the ic_snapshot writes yet (needs a redeploy).")
