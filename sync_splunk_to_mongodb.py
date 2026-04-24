from sshtunnel import SSHTunnelForwarder
import mysql.connector
from pymongo import MongoClient
import splunklib.client as client
import splunklib.results as results
import urllib3
import json
import os
from dotenv import load_dotenv

# Disable SSL Warnings for Splunk
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def sync_mysql_to_mongodb():
    """
    Fetch mapping data from MySQL 'reciever' table via SSH Tunnel.
    """
    SSH_HOST = os.getenv("SSH_HOST")
    SSH_PORT = int(os.getenv("SSH_PORT", 22))
    SSH_USER = os.getenv("SSH_USER")
    SSH_PASSWORD = os.getenv("SSH_PASSWORD")

    MYSQL_HOST = os.getenv("MYSQL_HOST")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
    MYSQL_USER = os.getenv("MYSQL_USER")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
    MYSQL_DB = os.getenv("MYSQL_DB", "monitor")

    try:
        with SSHTunnelForwarder(
            (SSH_HOST, SSH_PORT),
            ssh_username=SSH_USER,
            ssh_password=SSH_PASSWORD,
            remote_bind_address=(MYSQL_HOST, MYSQL_PORT),
        ) as tunnel:
            print(f"[*] SSH Tunnel established on local port: {tunnel.local_bind_port}")

            conn = mysql.connector.connect(
                host="127.0.0.1",
                port=tunnel.local_bind_port,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                database=MYSQL_DB,
                charset="utf8mb4",
            )

            cursor = conn.cursor(dictionary=True)
            sql_query = "SELECT _id, splunk_name, splunk_url, elk_name FROM reciever WHERE splunk_name IS NOT NULL AND splunk_name != ''"
            cursor.execute(sql_query)
            rows = cursor.fetchall()

            cursor.close()
            conn.close()

            if rows:
                print(f"[+] Found {len(rows)} records from MySQL.")
                return rows
            else:
                print("[-] No data found in MySQL table matching the criteria.")
                return []

    except Exception as e:
        print(f"[!] MySQL Error: {e}")
        return None


def run_sync():
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

    # 1. Fetch mapping data from MySQL
    mysql_data = sync_mysql_to_mongodb()
    if not mysql_data:
        print("[!] Stopping: Could not fetch mapping data.")
        return

    # Create Normalized Mapping
    splunk_name_to_info = {
        str(row["splunk_name"]).strip().lower(): {
            "reciever_id": row["_id"],
            "elk_name": row.get("elk_name"),
        }
        for row in mysql_data
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
