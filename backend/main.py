"""
main.py - FastAPI Application (Simple version - No Auth)
Install: pip install fastapi uvicorn paho-mqtt pymongo python-dotenv
Run: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
import asyncio
from datetime import timedelta
from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
import database
import utils
import mqtt_handler
import websocket_manager

# FastAPI app
app = FastAPI(title="Door Control API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic Models
class ControlCommand(BaseModel):
    command: str

# ==================== STARTUP/SHUTDOWN ====================
@app.on_event("startup")
async def startup_event():
    print("Starting Door Control API...")
    
    database.init_indexes()
    
    event_loop = asyncio.get_running_loop()
    mqtt_handler.start_mqtt(event_loop)
    
    # Load initial status
    status_doc = database.door_status.find_one({"_id": "current"})
    if status_doc:
        last_updated = status_doc.get("last_updated")
        if last_updated:
            last_updated_str = utils.format_datetime_for_response(last_updated)
        else:
            last_updated_str = utils.get_vietnam_time().isoformat()
        
        mqtt_handler.current_status.update({
            "status": status_doc.get("status", "unknown"),
            "distance": status_doc.get("distance", 0),
            "last_updated": last_updated_str
        })
    
    print("✓ Application started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    print("Shutting down...")
    mqtt_handler.stop_mqtt()
    database.close_connection()
    print("✓ Shutdown complete")

# ==================== ENDPOINTS ====================
@app.get("/")
async def root():
    return {
        "message": "Door Control API",
        "status": "running",
        "version": "2.0"
    }

@app.get("/api/status")
async def get_status():
    """Get current door status"""
    return JSONResponse(content=mqtt_handler.get_current_status())

@app.post("/api/control")
async def control_door(command: ControlCommand):
    """Control door"""
    valid_commands = ["open", "close", "stop", "auto_on", "auto_off"]

    if command.command not in valid_commands:
        raise HTTPException(status_code=400, detail="Invalid command")

    try:
        mqtt_handler.publish_command(command.command)
        
        if command.command in ["open", "close"]:
            mqtt_handler.set_manual_operation(True)
            
            database.door_events.insert_one({
                "timestamp": utils.get_vietnam_time(),
                "type": f"manual_{command.command}",
                "state": mqtt_handler.current_status.get("status"),
                "source": "web"
            })
            
    except Exception as e:
        print("Error controlling door:", e)
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True, "command": command.command}

@app.get("/api/history")
async def get_history(
    days: int = 7, 
    limit: int = 20, 
    skip: int = 0,
    date: str = None,          # Filter by specific date (YYYY-MM-DD)
    mode: str = None,          # Filter by mode: "manual", "auto", "all"
    hour_from: int = None,     # Filter by hour range (0-23)
    hour_to: int = None,
    min_duration: float = None,  # Filter by duration (seconds)
    max_duration: float = None
):
    """Get door sessions with filters"""
    
    # Build query
    query = {}
    
    # Date filter
    if date:
        # Specific date
        query["date"] = date
    else:
        # Date range (last N days)
        start_date = (utils.get_vietnam_time() - timedelta(days=days)).strftime("%Y-%m-%d")
        query["date"] = {"$gte": start_date}
    
    # Mode filter (manual/auto)
    if mode and mode != "all":
        if mode == "manual":
            query["is_manual"] = True
        elif mode == "auto":
            query["is_manual"] = False
    
    # Hour filter
    if hour_from is not None or hour_to is not None:
        query["hour"] = {}
        if hour_from is not None:
            query["hour"]["$gte"] = hour_from
        if hour_to is not None:
            query["hour"]["$lte"] = hour_to
    
    # Duration filter
    if min_duration is not None or max_duration is not None:
        query["duration_seconds"] = {}
        if min_duration is not None:
            query["duration_seconds"]["$gte"] = min_duration
        if max_duration is not None:
            query["duration_seconds"]["$lte"] = max_duration
    
    # Get sessions
    sessions = list(
        database.door_sessions.find(
            query,
            {"_id": 0}
        ).sort("open_time", -1).skip(skip).limit(limit)
    )

    for session in sessions:
        if "open_time" in session:
            session["open_time"] = utils.format_datetime_for_response(session["open_time"])
        if "close_time" in session:
            session["close_time"] = utils.format_datetime_for_response(session["close_time"])

    total = database.door_sessions.count_documents(query)

    return {
        "sessions": sessions,
        "total": total,
        "limit": limit,
        "skip": skip,
        "filters": {
            "date": date,
            "mode": mode,
            "hour_from": hour_from,
            "hour_to": hour_to,
            "min_duration": min_duration,
            "max_duration": max_duration
        }
    }

@app.get("/api/statistics")
async def get_statistics(days: int = 7):
    """Get door statistics"""
    start_date = utils.get_vietnam_time() - timedelta(days=days)
    today = utils.get_vietnam_date_str()
    
    pipeline = [
        {"$match": {"open_time": {"$gte": start_date}}},
        {"$group": {
            "_id": "$date",
            "open_count": {"$sum": 1},
            "total_duration": {"$sum": "$duration_seconds"},
            "avg_duration": {"$avg": "$duration_seconds"},
            "manual_count": {"$sum": {"$cond": ["$is_manual", 1, 0]}},
            "auto_count": {"$sum": {"$cond": ["$is_manual", 0, 1]}}
        }},
        {"$sort": {"_id": -1}},
        {"$limit": days}
    ]
    
    daily_stats = list(database.door_sessions.aggregate(pipeline))
    today_stats = next((s for s in daily_stats if s["_id"] == today), None)
    
    hourly_pipeline = [
        {"$match": {"datetime": {"$gte": start_date}}},
        {"$group": {
            "_id": "$hour",
            "total_opens": {"$sum": "$open_count"},
            "avg_duration": {"$avg": "$total_duration"}
        }},
        {"$sort": {"total_opens": -1}},
        {"$limit": 5}
    ]
    
    peak_hours = list(database.door_stats_hourly.aggregate(hourly_pipeline))
    
    total_opens = sum(s.get("open_count", 0) for s in daily_stats)
    total_duration = sum(s.get("total_duration", 0) for s in daily_stats)
    
    return {
        "period_days": days,
        "total_opens": total_opens,
        "total_duration_minutes": round(total_duration / 60, 1),
        "avg_duration_seconds": round(total_duration / max(total_opens, 1), 1),
        "today_opens": today_stats.get("open_count", 0) if today_stats else 0,
        "today_duration_minutes": round(today_stats.get("total_duration", 0) / 60, 1) if today_stats else 0,
        "daily_stats": daily_stats,
        "peak_hours": peak_hours
    }

@app.get("/api/events/recent")
async def get_recent_events(limit: int = 10):
    """Get recent important events"""
    events = list(
        database.door_events.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit)
    )

    for event in events:
        if "timestamp" in event:
            event["timestamp"] = utils.format_datetime_for_response(event["timestamp"])

    return {"events": events}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket_manager.handle_websocket_connection(
        websocket, 
        mqtt_handler.get_current_status()
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)