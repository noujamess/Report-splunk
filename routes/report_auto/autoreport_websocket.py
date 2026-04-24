import asyncio
import re
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
import splunklib.client as client
import splunklib.results as results
from pymongo import MongoClient

import os
import urllib3
from dotenv import load_dotenv

load_dotenv()

# Disable insecure request warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

router = APIRouter(prefix="/api/report_ws")
report_lock = asyncio.Lock()

# 1. Global MongoDB Client
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/")
DB_NAME = os.getenv("MONGO_DB_NAME", "report")
COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "customer")

try:
    mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    coll = db[COLLECTION_NAME]
except Exception as e:
    print(f"Failed to connect to MongoDB in WebSocket module: {e}")

# Default Splunk Credentials
SPLUNK_PORT = int(os.getenv("SPLUNK_PORT", 8089))
SPLUNK_USER = os.getenv("SPLUNK_USER", "admin")
SPLUNK_PASS = os.getenv("SPLUNK_PASS", "changeme")

# Limit Concurrent Splunk Jobs
MAX_CONCURRENT_SPLUNK_JOBS = 5
splunk_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SPLUNK_JOBS)

# Compile Regex globally
TABLE_FIELDS_PATTERN = re.compile(r"\|\s*(?:table|fields)\s+([^|]+)", re.IGNORECASE)


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
                            f.strip(",").strip('"')
                            for f in table_match.group(1).strip().split()
                            if f.strip() and not f.startswith("-")
                        ]
                        for row in data_rows:
                            for field in expected_fields:
                                if field not in row:
                                    row[field] = ""

                    msg = f"Collected {len(data_rows)} rows for {query_name}."
                    logs.append(msg)

                    result_data = {
                        "site_id": site_id,
                        "device": device if device else "Global",
                        "query_name": query_name,
                        "data_count": len(data_rows),
                        "results": data_rows,
                    }

                    await websocket.send_json({"type": "log", "message": msg})
                    await websocket.send_json({"type": "data", "data": result_data})
                    return result_data
                else:
                    msg = f"No results for {query_name}."
                    logs.append(msg)
                    await websocket.send_json({"type": "log", "message": msg})
                    return None
            finally:
                try:
                    await asyncio.to_thread(job.cancel)
                except Exception:
                    pass
        except Exception as e:
            msg = f"Error processing {query_name}: {e}"
            logs.append(msg)
            try:
                await websocket.send_json({"type": "log", "message": msg})
            except Exception:
                pass
            return None


@router.get("/sites")
async def get_sites():
    """Fetch unique site IDs from MongoDB for the new WebSocket frontend."""
    try:
        if coll is None:
            return {"status": "error", "message": "MongoDB connection not initialized"}

        # Test connection before distinct
        unique_ids = await asyncio.to_thread(coll.distinct, "id")
        if not unique_ids:
            return {
                "status": "success",
                "sites": [],
                "message": "No sites found in collection",
            }

        unique_ids = sorted([str(uid) for uid in unique_ids if uid])
        return {"status": "success", "sites": unique_ids}
    except Exception as e:
        print(f"DEBUG: get_sites error: {e}")
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")


@router.websocket("/generate")
async def ws_generate_report(websocket: WebSocket):
    """New WebSocket endpoint for real-time report generation."""
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
            site_ids = [s.strip() for s in site_ids_input.split(",") if s.strip()]

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
                elif " " in final_date:
                    return final_date.replace(" ", "T")
                return final_date

            earliest = format_splunk_time(start_date_in, "00:00:00")
            latest = format_splunk_time(end_date_in, "23:59:59")

            # Load Templates
            try:
                templates = {}
                docs = await asyncio.to_thread(lambda: list(db["queries"].find({})))
                for doc in docs:
                    device = doc.get("device")
                    site = doc.get("site")
                    q_name = doc.get("query_name")
                    q_template = doc.get("query_template")

                    if device and site and q_name and q_template is not None:
                        if device not in templates:
                            templates[device] = {}
                        if site not in templates[device]:
                            templates[device][site] = {}
                        templates[device][site][q_name] = q_template
            except Exception as e:
                await websocket.send_json(
                    {"type": "error", "message": f"Template error: {e}"}
                )
                return

            await websocket.send_json(
                {"type": "log", "message": f"Started report for {site_ids_input}"}
            )

            splunk_services = {}
            job_tasks = []

            for site_id in site_ids:
                site_records = list(coll.find({"id": site_id}))
                if not site_records:
                    continue

                splunk_host = site_records[0].get("splunk_server_host", "localhost")
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
                    except Exception as e:
                        await websocket.send_json(
                            {"type": "log", "message": f"Splunk Connection Error: {e}"}
                        )
                        continue

                service = splunk_services[splunk_host]

                # 1. Global Queries (Matched by Site ID)
                all_run_config = templates.get("all_run", {})
                global_queries = {}
                if site_id in all_run_config:
                    global_queries = all_run_config[site_id]
                elif "all" in all_run_config:
                    global_queries = all_run_config["all"]
                else:
                    # Compatibility with flat format
                    is_nested = any(
                        isinstance(v, dict) for v in all_run_config.values()
                    )
                    if not is_nested:
                        global_queries = all_run_config

                for q_name, q_template in global_queries.items():
                    if "{index}" in q_template:
                        for record in site_records:
                            final_query = (
                                q_template.replace("{index}", record.get("index", ""))
                                .replace("{id}", site_id)
                                .replace("{site_names}", site_id)
                            )
                            job_tasks.append(
                                run_splunk_job(
                                    service,
                                    final_query,
                                    earliest,
                                    latest,
                                    site_id,
                                    f"Global-{record.get('device', 'Unknown')}",
                                    q_name,
                                    logs,
                                    websocket,
                                )
                            )
                    else:
                        final_query = q_template.replace(
                            "{site_names}", site_id
                        ).replace("{id}", site_id)
                        job_tasks.append(
                            run_splunk_job(
                                service,
                                final_query,
                                earliest,
                                latest,
                                site_id,
                                "Global",
                                q_name,
                                logs,
                                websocket,
                            )
                        )

                # 2. Device Queries
                for record in site_records:
                    device = record.get("device")
                    idx = record.get("index")
                    device_config = templates.get(device, {})

                    device_queries = {}
                    if site_id in device_config:
                        device_queries = device_config[site_id]
                    elif "all" in device_config:
                        device_queries = device_config["all"]
                    else:
                        continue

                    for q_name, q_template in device_queries.items():
                        final_query = (
                            q_template.replace("{index}", idx)
                            .replace("{id}", site_id)
                            .replace("{site_names}", site_id)
                        )
                        job_tasks.append(
                            run_splunk_job(
                                service,
                                final_query,
                                earliest,
                                latest,
                                site_id,
                                device,
                                q_name,
                                logs,
                                websocket,
                            )
                        )

            if job_tasks:
                await websocket.send_json(
                    {"type": "log", "message": f"Executing {len(job_tasks)} queries..."}
                )
                results_list = await asyncio.gather(*job_tasks)
                all_report_data = [res for res in results_list if res is not None]
                await websocket.send_json(
                    {"type": "complete", "total": len(all_report_data)}
                )

    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        try:
            await websocket.send_json(
                {"type": "error", "message": f"Server Error: {str(e)}"}
            )
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
