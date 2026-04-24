import json
import pymongo
import os

# Configuration
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/")
DB_NAME = os.getenv("MONGO_DB_NAME", "report")
COLLECTION_NAME = os.getenv("MONGO_QUERY_COLLECTION_NAME", "queries")
JSON_FILE_PATH = "query.json"


def sync_query_to_mongo():
    try:
        # Connect to MongoDB
        client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]

        # Test connection
        client.server_info()
        print("Connected to MongoDB successfully.")

    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        return

    # Load JSON file
    if not os.path.exists(JSON_FILE_PATH):
        print(f"Error: File '{JSON_FILE_PATH}' not found in the current directory.")
        return

    with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
        try:
            query_data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON file: {e}")
            return

    inserted_count = 0
    updated_count = 0

    # Parse nested JSON: device -> site -> query_name -> query_template
    for device, sites in query_data.items():
        for site, queries in sites.items():
            for query_name, query_template in queries.items():
                # Setup filter for checking existing query
                filter_doc = {"device": device, "site": site, "query_name": query_name}

                # Document to update or insert
                update_doc = {
                    "$set": {
                        "device": device,
                        "site": site,
                        "query_name": query_name,
                        "query_template": query_template,
                    }
                }

                # Update if exists, insert if not (Upsert)
                result = collection.update_one(filter_doc, update_doc, upsert=True)

                if result.upserted_id:
                    inserted_count += 1
                elif result.modified_count > 0:
                    updated_count += 1

    print("-" * 30)
    print("Sync Summary:")
    print(f"Added new queries: {inserted_count}")
    print(f"Updated existing queries: {updated_count}")
    print("-" * 30)


if __name__ == "__main__":
    sync_query_to_mongo()
