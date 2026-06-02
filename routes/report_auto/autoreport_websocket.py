import asyncio
import os
import re

import splunklib.client as client
import splunklib.results as results
import urllib3
from dotenv import load_dotenv
from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pymongo import MongoClient


load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

router = APIRouter(prefix="/api/report_ws")
report_lock = asyncio.Lock()

# Configuration from environment variables
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/")
DB_NAME = os.getenv("MONGO_DB_NAME", "report")
COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "customer")
QUERY_COLLECTION_NAME = os.getenv("MONGO_QUERY_COLLECTION_NAME", "queries")

try:
    mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    coll = db[COLLECTION_NAME]
except Exception as exc:
    print(f"Failed to connect to MongoDB in WebSocket module: {exc}")
    db = None
    coll = None

SPLUNK_PORT = int(os.getenv("SPLUNK_PORT", 8089))
SPLUNK_USER = os.getenv("SPLUNK_USER", "admin")
SPLUNK_PASS = os.getenv("SPLUNK_PASS", "changeme")

ELK_SERVER_HOST = os.getenv("ELK_SERVER_HOST", "https://localhost:9200")
ELK_USER = os.getenv("ELK_USER", "admin")
ELK_PASS = os.getenv("ELK_PASS", "changeme")

MAX_CONCURRENT_SPLUNK_JOBS = 5
MAX_CONCURRENT_ELK_JOBS = 5

splunk_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SPLUNK_JOBS)
elk_semaphore = asyncio.Semaphore(MAX_CONCURRENT_ELK_JOBS)
TABLE_FIELDS_PATTERN = re.compile(r"\|\s*(?:table|fields)\s+([^|]+)", re.IGNORECASE)


def format_splunk_time(date_str, default_time):
    if not date_str or len(date_str) < 10:
        return date_str

    final_date = date_str
    try:
        if "/" in date_str:
            parts = date_str.split("/")
            if len(parts) == 3:
                p1, p2, p3 = int(parts[0]), int(parts[1]), parts[2]
                if p1 > 12:
                    final_date = f"{p3}-{parts[1]}-{parts[0]}"
                elif p2 > 12:
                    final_date = f"{p3}-{parts[0]}-{parts[1]}"
                else:
                    final_date = f"{p3}-{parts[1]}-{parts[0]}"
    except Exception:
        return date_str

    if "T" not in final_date and ":" not in final_date:
        return f"{final_date}T{default_time}"
    if " " in final_date:
        return final_date.replace(" ", "T")
    return final_date


def format_elk_time(date_str, default_time, fallback_relative):
    if not date_str:
        return fallback_relative
    if date_str == "+0m":
        return "now"
    if date_str.startswith("-"):
        return f"now{date_str}"
    if date_str.startswith("+"):
        return f"now{date_str}"
    return format_splunk_time(date_str, default_time)


async def load_queries_from_mongodb():
    return await asyncio.to_thread(
        lambda: list(db[QUERY_COLLECTION_NAME].find({"enabled": {"$ne": False}}))
    )


def replace_placeholders(template, site_id, record):
    receiver_id = (
        record.get("receiver_id")
        or record.get("reciever_id")
        or record.get("receiverId")
        or ""
    )
    replacements = {
        "{index}": str(record.get("index", "")),
        "{id}": str(site_id),
        "{site_names}": str(site_id),
        "{elk_name}": str(record.get("elk_name", "")),
        "{receiver_id}": str(receiver_id),
        "{reciever_id}": str(receiver_id),
    }

    final_value = template
    for key, value in replacements.items():
        final_value = final_value.replace(key, value)
    return final_value


def get_record_receiver_id(record):
    return (
        record.get("receiver_id")
        or record.get("reciever_id")
        or record.get("receiverId")
        or ""
    )


def get_site_context_record(site_records):
    for record in site_records:
        if get_record_receiver_id(record):
            return record
    return site_records[0] if site_records else {}


def build_query_maps(queries):
    splunk_global = {}
    splunk_device = {}
    elk_global = {}
    elk_device = {}

    for query in queries:
        if not query.get("enabled", True):
            continue

        source = query.get("source")
        device = query.get("device")
        site = query.get("site")
        query_name = query.get("query_name")

        if not source or not device or not site or not query_name:
            continue

        target_map = None
        if source == "splunk" and device == "all_run":
            target_map = splunk_global
        elif source == "splunk":
            target_map = splunk_device
        elif source == "elk" and device == "all":
            target_map = elk_global
        elif source == "elk":
            target_map = elk_device

        if target_map is None:
            continue

        target_map.setdefault(device, {})
        target_map[device].setdefault(site, [])
        target_map[device][site].append(query)

    return splunk_global, splunk_device, elk_global, elk_device


def get_site_queries(site_map, site_id):
    if site_id in site_map:
        return site_map[site_id]
    return site_map.get("all", [])


async def run_splunk_job(
    service,
    final_query,
    earliest,
    latest,
    site_id,
    device,
    query_name,
    logs,
    websocket: WebSocket,
):
    async with splunk_semaphore:
        try:
            job = await asyncio.to_thread(
                service.jobs.create,
                final_query,
                earliest_time=earliest,
                latest_time=latest,
                adhoc_search_level="fast",
                timeout=240,
            )

            max_wait = 300
            waited = 0

            try:
                while True:
                    is_done = await asyncio.to_thread(job.is_done)
                    if is_done:
                        break

                    if waited >= max_wait:
                        msg = f"Timeout: Splunk job {query_name} took too long."
                        logs.append(msg)
                        await websocket.send_json({"type": "log", "message": msg})
                        return None

                    await asyncio.sleep(1)
                    waited += 1

                result_count = int(job["resultCount"])
                if result_count > 0:
                    result_stream = await asyncio.to_thread(
                        job.results, output_mode="json", count=0
                    )
                    reader = results.JSONResultsReader(result_stream)
                    data_rows = [item for item in reader if isinstance(item, dict)]

                    table_match = TABLE_FIELDS_PATTERN.search(final_query)
                    if table_match:
                        expected_fields = [
                            field.strip(",").strip('"')
                            for field in table_match.group(1).strip().split()
                            if field.strip() and not field.startswith("-")
                        ]
                        for row in data_rows:
                            for field in expected_fields:
                                if field not in row:
                                    row[field] = ""

                    msg = f"Collected {len(data_rows)} rows for Splunk query {query_name}."
                    logs.append(msg)
                    result_data = {
                        "source": "splunk",
                        "site_id": site_id,
                        "device": device if device else "Global",
                        "query_name": query_name,
                        "data_count": len(data_rows),
                        "results": data_rows,
                    }

                    await websocket.send_json({"type": "log", "message": msg})
                    await websocket.send_json({"type": "data", "data": result_data})
                    return result_data

                msg = f"No results for Splunk query {query_name}."
                logs.append(msg)
                await websocket.send_json({"type": "log", "message": msg})
                return None
            finally:
                try:
                    await asyncio.to_thread(job.cancel)
                except Exception:
                    pass
        except Exception as exc:
            msg = f"Error processing Splunk query {query_name}: {exc}"
            logs.append(msg)
            try:
                await websocket.send_json({"type": "log", "message": msg})
            except Exception:
                pass
            return None


async def run_elk_job(
    es_client,
    query_config,
    site_id,
    device,
    query_name,
    final_query_string,
    earliest,
    latest,
    logs,
    websocket: WebSocket,
):
    async with elk_semaphore:
        try:
            group_by_field = query_config.get("group_by_field", "usecase.keyword")
            index_pattern = query_config.get("index_pattern", "casecbt-v01")
            time_field = query_config.get("time_field", "@timestamp")

            request_body = {
                "size": 0,
                "query": {
                    "bool": {
                        "must": [
                            {"query_string": {"query": final_query_string}},
                            {
                                "range": {
                                    time_field: {
                                        "gte": earliest,
                                        "lte": latest,
                                    }
                                }
                            },
                        ]
                    }
                },
                "aggs": {
                    "by_usecase": {
                        "terms": {
                            "field": group_by_field,
                            "size": 1000,
                        }
                    }
                },
            }

            response = await es_client.search(
                index=index_pattern,
                body=request_body,
                ignore_unavailable=True,
                allow_no_indices=True,
            )

            buckets = response.get("aggregations", {}).get("by_usecase", {}).get(
                "buckets", []
            )
            hits_total = response.get("hits", {}).get("total", {})
            if isinstance(hits_total, dict):
                total_hits = hits_total.get("value", 0)
            else:
                total_hits = hits_total or 0

            data_rows = [
                {
                    "usecase": bucket.get("key", ""),
                    "count": bucket.get("doc_count", 0),
                }
                for bucket in buckets
            ]

            if data_rows:
                msg = f"Collected {len(data_rows)} rows for ELK query {query_name}."
                logs.append(msg)
                result_data = {
                    "source": "elk",
                    "site_id": site_id,
                    "device": device,
                    "query_name": query_name,
                    "index_pattern": index_pattern,
                    "data_count": len(data_rows),
                    "results": data_rows,
                }
                await websocket.send_json({"type": "log", "message": msg})
                await websocket.send_json({"type": "data", "data": result_data})
                return result_data

            msg = f"No results for ELK query {query_name}."
            if total_hits:
                msg = (
                    f"No aggregation rows for ELK query {query_name}, "
                    f"but matched {total_hits} documents."
                )
            logs.append(msg)
            await websocket.send_json({"type": "log", "message": msg})
            return None
        except Exception as exc:
            msg = f"Error processing ELK query {query_name}: {exc}"
            logs.append(msg)
            try:
                await websocket.send_json({"type": "log", "message": msg})
            except Exception:
                pass
            return None


@router.get("/sites")
async def get_sites():
    try:
        if coll is None:
            return {"status": "error", "message": "MongoDB connection not initialized"}

        unique_ids = await asyncio.to_thread(coll.distinct, "id")
        if not unique_ids:
            return {
                "status": "success",
                "sites": [],
                "message": "No sites found in collection",
            }

        unique_ids = sorted([str(uid) for uid in unique_ids if uid])
        return {"status": "success", "sites": unique_ids}
    except Exception as exc:
        print(f"DEBUG: get_sites error: {exc}")
        raise HTTPException(status_code=500, detail=f"Database Error: {str(exc)}")


@router.websocket("/generate")
async def ws_generate_report(websocket: WebSocket):
    await websocket.accept()
    logs = []
    try:
        payload_data = await websocket.receive_json()
        site_ids_input = payload_data.get("site_names")
        start_date_in = payload_data.get("start_date", "-1h")
        end_date_in = payload_data.get("end_date", "+0m")

        if not site_ids_input:
            await websocket.send_json(
                {"type": "error", "message": "Site ID is required."}
            )
            await websocket.close()
            return

        async with report_lock:
            site_ids = [site.strip() for site in site_ids_input.split(",") if site.strip()]
            splunk_earliest = format_splunk_time(start_date_in, "00:00:00")
            splunk_latest = format_splunk_time(end_date_in, "23:59:59")
            elk_earliest = format_elk_time(start_date_in, "00:00:00", "now-1h")
            elk_latest = format_elk_time(end_date_in, "23:59:59", "now")

            try:
                unified_queries = await load_queries_from_mongodb()
                (
                    splunk_global,
                    splunk_device,
                    elk_global,
                    elk_device,
                ) = build_query_maps(unified_queries)
            except Exception as exc:
                await websocket.send_json(
                    {"type": "error", "message": f"Template error: {exc}"}
                )
                return

            await websocket.send_json(
                {"type": "log", "message": f"Started report for {site_ids_input}"}
            )
            await websocket.send_json(
                {
                    "type": "log",
                    "message": (
                        f"Loaded {len(unified_queries)} enabled queries from "
                        f"MongoDB collection '{QUERY_COLLECTION_NAME}'."
                    ),
                }
            )

            splunk_services = {}
            splunk_job_tasks = []
            elk_job_tasks = []

            es_auth = None
            if ELK_USER and ELK_PASS:
                es_auth = (ELK_USER, ELK_PASS)

            async with AsyncElasticsearch(
                ELK_SERVER_HOST,
                basic_auth=es_auth,
                verify_certs=False,
            ) as es_client:
                for site_id in site_ids:
                    site_records = await asyncio.to_thread(
                        lambda: list(coll.find({"id": site_id}))
                    )
                    if not site_records:
                        await websocket.send_json(
                            {
                                "type": "log",
                                "message": f"Site ID '{site_id}' not found in MongoDB.",
                            }
                        )
                        continue

                    splunk_host = site_records[0].get(
                        "splunk_server_host", "localhost"
                    )
                    if splunk_host not in splunk_services:
                        await websocket.send_json(
                            {
                                "type": "log",
                                "message": f"Connecting to Splunk: {splunk_host}...",
                            }
                        )
                        try:
                            service = await asyncio.to_thread(
                                client.connect,
                                host=splunk_host,
                                port=SPLUNK_PORT,
                                username=SPLUNK_USER,
                                password=SPLUNK_PASS,
                                scheme="https",
                                verify=False,
                            )
                            splunk_services[splunk_host] = service
                        except Exception as exc:
                            await websocket.send_json(
                                {
                                    "type": "log",
                                    "message": f"Splunk Connection Error: {exc}",
                                }
                            )
                            continue

                    service = splunk_services[splunk_host]
                    site_context_record = get_site_context_record(site_records)

                    for query in get_site_queries(
                        splunk_global.get("all_run", {}), site_id
                    ):
                        query_name = query.get("query_name")
                        query_template = query.get("query_template", "")

                        if "{index}" in query_template:
                            for record in site_records:
                                final_query = replace_placeholders(
                                    query_template, site_id, record
                                )
                                splunk_job_tasks.append(
                                    run_splunk_job(
                                        service,
                                        final_query,
                                        splunk_earliest,
                                        splunk_latest,
                                        site_id,
                                        f"Global-{record.get('device', 'Unknown')}",
                                        query_name,
                                        logs,
                                        websocket,
                                    )
                                )
                        else:
                            final_query = replace_placeholders(
                                query_template, site_id, site_records[0]
                            )
                            splunk_job_tasks.append(
                                run_splunk_job(
                                    service,
                                    final_query,
                                    splunk_earliest,
                                    splunk_latest,
                                    site_id,
                                    "Global",
                                    query_name,
                                    logs,
                                    websocket,
                                )
                            )

                    for query in get_site_queries(elk_global.get("all", {}), site_id):
                        query_name = query.get("query_name")
                        receiver_id = get_record_receiver_id(site_context_record)
                        if not receiver_id:
                            msg = (
                                f"Skipping ELK query {query_name} for site {site_id}: "
                                "receiver_id not found in MongoDB."
                            )
                            logs.append(msg)
                            await websocket.send_json({"type": "log", "message": msg})
                            continue

                        final_query_string = replace_placeholders(
                            query.get("query_template", ""), site_id, site_context_record
                        )
                        elk_job_tasks.append(
                            run_elk_job(
                                es_client,
                                query,
                                site_id,
                                site_context_record.get("device", "Global"),
                                query_name,
                                final_query_string,
                                elk_earliest,
                                elk_latest,
                                logs,
                                websocket,
                            )
                        )

                    for record in site_records:
                        device = record.get("device")
                        device_queries = get_site_queries(
                            splunk_device.get(device, {}), site_id
                        )
                        for query in device_queries:
                            query_name = query.get("query_name")
                            query_template = query.get("query_template", "")
                            final_query = replace_placeholders(
                                query_template, site_id, record
                            )
                            splunk_job_tasks.append(
                                run_splunk_job(
                                    service,
                                    final_query,
                                    splunk_earliest,
                                    splunk_latest,
                                    site_id,
                                    device,
                                    query_name,
                                    logs,
                                    websocket,
                                )
                            )

                        elk_queries = get_site_queries(elk_device.get(device, {}), site_id)
                        for query in elk_queries:
                            query_name = query.get("query_name")
                            final_query_string = replace_placeholders(
                                query.get("query_template", ""), site_id, record
                            )
                            elk_job_tasks.append(
                                run_elk_job(
                                    es_client,
                                    query,
                                    site_id,
                                    device,
                                    query_name,
                                    final_query_string,
                                    elk_earliest,
                                    elk_latest,
                                    logs,
                                    websocket,
                                )
                            )

                all_tasks = splunk_job_tasks + elk_job_tasks
                if all_tasks:
                    await websocket.send_json(
                        {
                            "type": "log",
                            "message": (
                                f"Executing {len(all_tasks)} queries "
                                f"(Splunk={len(splunk_job_tasks)}, ELK={len(elk_job_tasks)})..."
                            ),
                        }
                    )
                    results_list = await asyncio.gather(*all_tasks)
                    all_report_data = [
                        result for result in results_list if result is not None
                    ]
                    await websocket.send_json(
                        {"type": "complete", "total": len(all_report_data)}
                    )
                else:
                    await websocket.send_json({"type": "complete", "total": 0})

    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as exc:
        try:
            await websocket.send_json(
                {"type": "error", "message": f"Server Error: {str(exc)}"}
            )
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
