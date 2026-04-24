from pymongo import MongoClient
import splunklib.client as client
import splunklib.results as results
import urllib3
import json
import os
from dotenv import load_dotenv

# Disable SSL Warnings for Splunk
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MAPPING_FILE = "customer_mapping.json"

def load_customer_mapping():
    """
    Load mapping data from a local JSON file.
    Expected format: List of objects with splunk_name, reciever_id, and elk_name.
    """
    if not os.path.exists(MAPPING_FILE):
        print(f"[!] Mapping file {MAPPING_FILE} not found.")
        return []
    
    try:
        with open(MAPPING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[!] Error loading mapping file: {e}")
        return []

def run_sync():
    # Load environment variables
    load_dotenv()

    # MongoDB Configuration
    MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/")
    mongo_client = MongoClient(MONGO_URL)
    db = mongo_client[os.getenv("MONGO_DB_NAME", "report")]
    coll = db[os.getenv("MONGO_COLLECTION_NAME", "customer")]

    username = os.getenv("SPLUNK_USER", "admin")
    password = os.getenv("SPLUNK_PASS", "changeme")

    # Splunk Servers Configuration
    splunk_host_env = os.getenv("SPLUNK_HOSTS", "localhost")
    splunk_hosts = [h.strip() for h in splunk_host_env.split(",")]
    
    SPLUNK_SERVERS = []
    for host in splunk_hosts:
        SPLUNK_SERVERS.append({
            "host": host,
            "port": int(os.getenv("SPLUNK_PORT", 8089)),
            "username": username,
            "password": password,
        })

    # 1. Fetch mapping data from JSON file
    mapping_data = load_customer_mapping()
    if not mapping_data:
        print("[!] Stopping: No mapping data available.")
        return

    # Create Normalized Mapping
    # Format: { splunk_name_lowercase: { reciever_id, elk_name } }
    splunk_name_to_info = {
        str(row["splunk_name"]).strip().lower(): {
            "reciever_id": row.get("reciever_id"),
            "elk_name": row.get("elk_name"),
        }
        for row in mapping_data
        if row.get("splunk_name")
    }

    # 2. Process each Splunk Server
    for server_config in SPLUNK_SERVERS:
        HOST = server_config["host"]
        PORT = server_config["port"]
        USERNAME = server_config["username"]
        PASSWORD = server_config["password"]

        print(f"\n[*] Processing Splunk Server: {HOST}")

        try:
            service = client.connect(
                host=HOST,
                port=PORT,
                username=USERNAME,
                password=PASSWORD,
                scheme="https",
                verify=False,
            )

            query = """|tstats summariesonly=t count where index=* by index
            | rex field=index "[^,]+\\_(?<id>[^,]+)"
            | rex field=index "(?<device>[^,]+)\\_[^,]+"
            | search NOT index IN ("*alerts*", "*cim_modactions*", "*threat_activity*")
            | stats count by device id index
            | table index id device """

            kwargs_oneshot = {
                "earliest_time": "-1d@d",
                "latest_time": "+0m@m",
                "output_mode": "json",
            }

            oneshotsearch_results = service.jobs.oneshot(query, **kwargs_oneshot)
            reader = results.JSONResultsReader(oneshotsearch_results)

            server_count = 0
            for item in reader:
                if isinstance(item, dict):
                    splunk_id_raw = item.get("id")
                    splunk_id_norm = (
                        str(splunk_id_raw).strip().lower() if splunk_id_raw else None
                    )

                    # Matching Logic
                    if splunk_id_norm and splunk_id_norm in splunk_name_to_info:
                        info = splunk_name_to_info[splunk_id_norm]

                        # Enrich Data
                        item["elk_name"] = info.get("elk_name")
                        item["reciever_id"] = info.get("reciever_id")
                        item["splunk_server_host"] = HOST

                        # MongoDB Ops
                        filter_query = {
                            "index": item.get("index"),
                            "id": item.get("id"),
                            "device": item.get("device"),
                            "splunk_server_host": HOST,
                        }

                        update_result = coll.update_one(
                            filter_query, {"$set": item}, upsert=True
                        )

                        if (
                            update_result.upserted_id
                            or update_result.modified_count > 0
                        ):
                            server_count += 1

            print(f"[+] Server {HOST}: Updated/Inserted {server_count} records.")

        except Exception as e:
            print(f"[!] Error on Server {HOST}: {e}")

    print("\n[***] Sync Process Completed.")


if __name__ == "__main__":
    run_sync()
