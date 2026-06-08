import os
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient

router = APIRouter()

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/")
DB_NAME = os.getenv("MONGO_DB_NAME", "report")
QUERY_COLLECTION_NAME = os.getenv("MONGO_QUERY_COLLECTION_NAME", "queries")

try:
    mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    query_coll = db[QUERY_COLLECTION_NAME]
except Exception as exc:
    print(f"Failed to connect to MongoDB in api_manage_queries: {exc}")
    query_coll = None


class QueryModel(BaseModel):
    source: str
    query_type: str
    device: str
    site: str
    query_name: str
    query_template: str
    elk_server: Optional[str] = "primary"
    index_pattern: Optional[str] = None
    time_field: Optional[str] = None
    group_by_field: Optional[str] = None
    result_fields: Optional[List[str]] = None
    enabled: bool = True


def serialize_doc(doc):
    doc["_id"] = str(doc["_id"])
    return doc


@router.get("/")
async def get_all_queries():
    try:
        if query_coll is None:
            raise HTTPException(status_code=500, detail="MongoDB query collection is not initialized.")
        queries = list(query_coll.find({}))
        return {"status": "success", "data": [serialize_doc(q) for q in queries]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/")
async def create_query(query: QueryModel):
    try:
        if query_coll is None:
            raise HTTPException(status_code=500, detail="MongoDB query collection is not initialized.")
        result = query_coll.insert_one(query.model_dump())
        return {"status": "success", "id": str(result.inserted_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{query_id}")
async def update_query(query_id: str, query: QueryModel):
    try:
        if query_coll is None:
            raise HTTPException(status_code=500, detail="MongoDB query collection is not initialized.")
        if not ObjectId.is_valid(query_id):
            raise HTTPException(status_code=400, detail="Invalid Query ID")

        result = query_coll.update_one(
            {"_id": ObjectId(query_id)},
            {"$set": query.model_dump()},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Query not found")
        return {"status": "success", "message": "Query updated successfully"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{query_id}")
async def delete_query(query_id: str):
    try:
        if query_coll is None:
            raise HTTPException(status_code=500, detail="MongoDB query collection is not initialized.")
        if not ObjectId.is_valid(query_id):
            raise HTTPException(status_code=400, detail="Invalid Query ID")

        result = query_coll.delete_one({"_id": ObjectId(query_id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Query not found")
        return {"status": "success", "message": "Query deleted successfully"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
