# Project Report Deploy

Deployable version of the Project Report backend for teams who want to run the reporting API in their own environment.

This folder is designed to be:

- production-like in behavior
- free of real credentials
- configurable through `.env`
- ready for Splunk, MongoDB, and single or multi-ELK deployments

## Included capabilities

- WebSocket API for real-time report execution logs
- Query management API backed by MongoDB
- Customer/site/device lookup APIs
- Customer sync script for MongoDB population
- Query sync script for importing `query.json` into MongoDB
- Support for ELK `primary` and `secondary` routing

## Folder highlights

- `main.py`
  Starts the FastAPI application and exposes all routers.
- `routes/report_auto/autoreport_websocket.py`
  Main WebSocket and helper APIs such as `/sites`, `/devices`, `/customers`.
- `routes/report_auto/api_manage_queries.py`
  CRUD API for MongoDB query definitions.
- `query.json`
  Example query definitions for Splunk and ELK.
- `sync_queries_to_mongo.py`
  Imports example queries into MongoDB.
- `sync_splunk_to_mongodb.py`
  Populates customer mapping/index data into MongoDB.
- `customer_mapping.json`
  Example mapping file for customer metadata used by the sync script.

## Quick start

### 1. Create your environment file

Copy `.env.example` to `.env` and fill in your own values.

```bash
cp .env.example .env
```

Required areas to review:

- MongoDB connection
- Splunk credentials
- Splunk hosts
- Primary ELK connection
- Secondary ELK connection if used
- Allowed CORS origins

### 2. Install locally

```bash
pip install -r requirements.txt
```

### 3. Run the API

```bash
python main.py
```

By default the service listens on:

- `http://0.0.0.0:5000`

### 4. Run with Docker

```bash
docker-compose up -d --build
```

Default published port in `docker-compose.yml`:

- `33322`

Health check:

- `GET http://<host>:33322/`

## Main API endpoints

### Health

- `GET /`

### Real-time WebSocket report generation

- `WS /api/report_ws/generate`

Send JSON:

```json
{
  "site_names": "customer_a,customer_b",
  "start_date": "01/06/2026",
  "end_date": "30/06/2026"
}
```

### Query management

- `GET /api/manage/queries`
- `POST /api/manage/queries`
- `PUT /api/manage/queries/{query_id}`
- `DELETE /api/manage/queries/{query_id}`

### Lookup APIs

- `GET /api/report_ws/sites`
- `GET /api/report_ws/devices`
- `GET /api/report_ws/customers`
- `GET /api/report_ws/customers?site=customer_a`

### Customer sync API

- `POST /api/report_ws/customers/sync`
- `GET /api/report_ws/customers/sync`

## Query definition format

Queries are stored in MongoDB collection defined by `MONGO_QUERY_COLLECTION_NAME`.

Supported examples are included in `query.json`.

### Splunk example

```json
{
  "source": "splunk",
  "query_type": "splunk_search",
  "device": "xxx",
  "site": "all",
  "query_name": "xxx",
  "query_template": "search index={index}",
  "enabled": true
}
```

### ELK example on primary

```json
{
  "source": "elk",
  "query_type": "elk_search",
  "device": "all",
  "site": "all",
  "query_name": "",
  "query_template": "",
  "index_pattern": "",
  "time_field": "",
  "group_by_field": "",
  "result_fields": ["", ""],
  "elk_server": "",
  "enabled": true
}
```

### ELK example on secondary

```json
{
  "source": "elk",
  "query_type": "elk_search",
  "device": "",
  "site": "all",
  "query_name": "",
  "query_template": "",
  "index_pattern": "{elk_name}",
  "time_field": "@timestamp",
  "group_by_field": "",
  "result_fields": ["", "", "", "", ""],
  "elk_server": "secondary",
  "enabled": true
}
```

## Supported placeholders

The backend can replace these placeholders at runtime:

- `{index}`
- `{id}`
- `{id_upper}`
- `{site_names}`
- `{elk_name}

## Import example queries into MongoDB

```bash
python sync_queries_to_mongo.py
```

This reads `query.json` and upserts records into MongoDB.

## Sync customer data into MongoDB

```bash
python sync_splunk_to_mongodb.py
```

What it does:

- reads `customer_mapping.json`
- connects to each Splunk host in `SPLUNK_HOSTS`
- discovers indexes and device/site mappings
- enriches data with `reciever_id` and `elk_name`
- stores records in the MongoDB customer collection

## Notes for future deployers

- No real credentials are stored in this folder
- Existing ELK queries without `elk_server` default to `primary`
- You can remove the secondary ELK env values if your deployment uses only one ELK cluster
- `customer_mapping.json` is only an example and should be replaced with your own mapping

## Recommended deployment flow

1. Configure `.env`
2. Replace `customer_mapping.json` with your real mapping
3. Start MongoDB
4. Start this API
5. Run `python sync_splunk_to_mongodb.py`
6. Review customer data via `/api/report_ws/customers`
7. Edit `query.json` with your own queries
8. Run `python sync_queries_to_mongo.py`
9. Test `WS /api/report_ws/generate`
