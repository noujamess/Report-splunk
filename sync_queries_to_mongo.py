import os
import json
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

# Load environment variables
load_dotenv()

# Configuration from environment variables
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/")
DB_NAME = os.getenv("MONGO_DB_NAME", "report")
QUERY_COLLECTION_NAME = os.getenv("MONGO_QUERY_COLLECTION_NAME", "queries")
DEFAULT_QUERY_FILE = Path(__file__).resolve().parent / "query.json"


def load_query_documents(query_file):
    if not os.path.exists(query_file):
        raise FileNotFoundError(f"Error: Query file '{query_file}' not found.")

    with open(query_file, "r", encoding="utf-8") as file:
        try:
            payload = json.load(file)
        except json.JSONDecodeError as e:
            raise ValueError(f"Error parsing JSON file: {e}")

    queries = payload.get("queries", [])
    if not isinstance(queries, list):
        raise ValueError("Expected 'queries' to be a list in query file.")
    return queries


def build_upsert_operations(queries):
    operations = []
    for query in queries:
        source = query.get("source")
        device = query.get("device")
        site = query.get("site")
        query_name = query.get("query_name")

        if not source or not device or not site or not query_name:
            continue

        filter_doc = {
            "source": source,
            "device": device,
            "site": site,
            "query_name": query_name,
        }
        operations.append(UpdateOne(filter_doc, {"$set": query}, upsert=True))
    return operations


def sync_queries_to_mongodb(query_file, mongo_url, db_name, collection_name):
    try:
        queries = load_query_documents(query_file)
    except Exception as e:
        print(f"[!] {e}")
        return

    operations = build_upsert_operations(queries)

    if not operations:
        print("[!] No valid query documents found to import.")
        return

    try:
        mongo_client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
        db = mongo_client[db_name]
        collection = db[collection_name]

        # Test connection
        mongo_client.server_info()
        print("[*] Connected to MongoDB successfully.")
    except Exception as e:
        print(f"[!] Failed to connect to MongoDB: {e}")
        return

    result = collection.bulk_write(operations, ordered=False)
    print("-" * 40)
    print("Sync Summary:")
    print(f"Source file: {query_file}")
    print(f"Target collection: {db_name}.{collection_name}")
    print(f"Processed queries: {len(operations)}")
    print(f"Inserted: {result.upserted_count}")
    print(f"Modified: {result.modified_count}")
    print(f"Matched: {result.matched_count}")
    print("-" * 40)


if __name__ == "__main__":
    sync_queries_to_mongodb(
        query_file=str(DEFAULT_QUERY_FILE),
        mongo_url=MONGO_URL,
        db_name=DB_NAME,
        collection_name=QUERY_COLLECTION_NAME,
    )
