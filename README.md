# Splunk Auto Report Microservice

This microservice provides an automated reporting API and a real-time WebSocket connection to generate reports from Splunk and save/retrieve configurations from MongoDB.

## Features
- **FastAPI** backend for high performance and async capabilities.
- **WebSocket** support for real-time log streaming and report generation status.
- Configurable **CORS** and dynamic database connections.
- Ready to deploy as a standalone Docker container.

## Pre-requisites
- Docker and Docker Compose
- Or Python 3.9+ for local development

## Setup and Deployment (Docker)

1. **Configure Environment Variables**
   Rename `.env.example` to `.env` and fill in your actual credentials and connection URLs.
   ```bash
   cp .env.example .env
   ```

2. **Run the Service**
   Use Docker Compose to build and start the service in detached mode:
   ```bash
   docker-compose up -d --build
   ```

3. **Check the Status**
   The API will be available at `http://<your-server-ip>:33322` by default.
   You can verify it's running by accessing `http://<your-server-ip>:33322/`

## Configuration Options (`.env`)
- `PORT`: Internal port the FastAPI app listens on (default: 5000).
- `ALLOWED_ORIGINS`: Comma-separated list of origins allowed to access the API (e.g., `http://example.com,http://localhost:3000`). Use `*` to allow all.
- `MONGO_URL`: Full MongoDB connection string (e.g., `mongodb://localhost:27017/`).
- `MONGO_DB_NAME` / `MONGO_COLLECTION_NAME`: Target database and collection names.
- `SPLUNK_USER` / `SPLUNK_PASS`: Service account credentials for querying Splunk.
- `SPLUNK_HOSTS`: Comma-separated list of Splunk Server IPs for data syncing (e.g., `192.168.4.101,192.168.4.102`).

## Managing Queries (`query.json`)
The `query.json` file serves as a template or configuration mapping for your Splunk queries. It follows a 3-level nested structure:
`"Device Type" -> "Site ID" -> "Query Name" : "Splunk Query"`

**Example Structure:**
```json
{
    "fortigate": {
        "all": {
            "Fortigate_Auth_Success": "search index={index} \"logged in successfully\" | stats count"
        },
        "specific_site": {
            "Custom_Query": "search index={index} | head 10"
        }
    }
}
```
**Placeholders:**
- `{index}`: Will be dynamically replaced by the target Splunk index.
- `{site_names}` or `{id}`: Will be dynamically replaced by the Site ID.

### Syncing Queries to MongoDB
Once you have defined your templates in `query.json`, you must sync them to MongoDB so the WebSocket can read them dynamically.
Run the sync script:
```bash
python sync_queries_to_mongo.py
```

## Syncing Splunk Indexes and Customer Data
To sync mapping data between Splunk and MongoDB, ensure `customer_mapping.json` is updated with your customer details and run:
```bash
python sync_splunk_to_mongodb.py
```

### Customer Mapping (`customer_mapping.json`)
This file defines the relationship between Splunk index identifiers and customer metadata.
```json
[
    {
        "splunk_name": "customer_id",
        "reciever_id": "metadata_id",
        "elk_name": "related_name"
    }
]
```

## Using this as a Module
If you have another backend service, you can connect to this microservice via HTTP requests or standard WebSockets.

**WebSocket Endpoint:** 
`ws://<ip>:33322/api/report_ws/generate`

**REST API Examples:**
`GET http://<ip>:33322/api/report_ws/sites` - Fetches unique site IDs from MongoDB.
