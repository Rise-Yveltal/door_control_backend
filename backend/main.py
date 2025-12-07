"""
Backend Server - FastAPI + MQTT + MongoDB (FIXED)
Install: pip install fastapi uvicorn paho-mqtt pymongo python-dotenv
Run: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import asyncio
import json
import paho.mqtt.client as mqtt
from pymongo import MongoClient, DESCENDING
import os
from dotenv import load_dotenv
import traceback

load_dotenv()

# Configuration
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
MQTT_BROKER = os.getenv("MQTT_BROKER", "myiot-mqtt.cloud.shiftr.io")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "myiot-mqtt")
MQTT_PASS = os.getenv("MQTT_PASS", "IiIJDp6KZ66NMlJz")

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

# MongoDB connection
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client["door_system"]
events_collection = db["door_events"]
status_collection = db["door_status"]
stats_collection = db["daily_statistics"]

# Create indexes
events_collection.create_index([("timestamp", DESCENDING)])
events_collection.create_index([("event_type", 1), ("timestamp", DESCENDING)])

# WebSocket connections
active_connections: List[WebSocket] = []

# Current status cache
current_status = {
    "status": "unknown",
    "distance": 0,
    "last_updated": datetime.utcnow().isoformat(),
    "auto_mode": True
}

# Pydantic models
class ControlCommand(BaseModel):
    command: str

class EventResponse(BaseModel):
    timestamp: str
    event_type: str
    status: str
    distance: Optional[int] = None
    trigger_source: Optional[str] = None
    duration: Optional[float] = None

# MQTT Client
mqtt_client = mqtt.Client()
mqtt_connected = False
event_loop: Optional[asyncio.AbstractEventLoop] = None

def on_mqtt_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        print("✓ Connected to MQTT Broker!")
        mqtt_connected = True
        client.subscribe("door/#")
        print("  Subscribed to: door/#")
    else:
        print(f"✗ Failed to connect to MQTT, return code {rc}")
        mqtt_connected = False

def on_mqtt_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode(errors="ignore")
    print(f"MQTT [{topic}]: {payload}")

    global event_loop
    if event_loop is None:
        print("‼️ Event loop not ready yet - skipping MQTT message")
        return

    try:
        future = asyncio.run_coroutine_threadsafe(process_mqtt_message(topic, payload), event_loop)
    except Exception as e:
        print("Error scheduling MQTT message processing:", e)
        traceback.print_exc()

mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message

async def process_mqtt_message(topic, payload):
    global current_status
    timestamp = datetime.utcnow()

    try:
        # ---- door/status ----
        if topic == "door/status":
            try:
                parsed = json.loads(payload)
                status_val = parsed.get("status") or parsed.get("state") or parsed.get("currentState")
                distance = parsed.get("distance")
            except Exception:
                status_val = payload.strip().lower()
                distance = None

            current_status["status"] = status_val
            current_status["last_updated"] = timestamp.isoformat()
            if distance is not None:
                current_status["distance"] = distance

            status_update = {
                "$set": {
                    "status": status_val,
                    "last_updated": timestamp
                },
                "$inc": {"total_updates": 1}
            }
            if distance is not None:
                status_update["$set"]["sensor_distance"] = distance

            status_collection.update_one({"_id": "current"}, status_update, upsert=True)

            # FIX: Đếm cả "opening" và "open" là mở cửa
            if status_val in ["open", "opening"]:
                event_type = "door_opened"
                event_doc = {
                    "timestamp": timestamp,
                    "event_type": event_type,
                    "status": status_val,
                    "trigger_source": "auto"
                }
                events_collection.insert_one(event_doc)
                update_daily_stats(event_type, timestamp)
                print(f"✓ Logged open event: {status_val}")
            
            elif status_val in ["closed", "closing"]:
                event_type = "door_closed"
                event_doc = {
                    "timestamp": timestamp,
                    "event_type": event_type,
                    "status": status_val,
                    "trigger_source": "auto"
                }
                events_collection.insert_one(event_doc)
                update_daily_stats(event_type, timestamp)
                print(f"✓ Logged close event: {status_val}")

        # ---- door/sensor ----
        elif topic == "door/sensor":
            try:
                try:
                    p = json.loads(payload)
                    distance = int(p.get("distance", p)) if isinstance(p, dict) else int(p)
                except Exception:
                    distance = int(payload)
                current_status["distance"] = distance
                current_status["last_updated"] = timestamp.isoformat()

                status_collection.update_one(
                    {"_id": "current"},
                    {"$set": {"sensor_distance": distance, "last_updated": timestamp}},
                    upsert=True
                )
            except Exception:
                pass

        # ---- door/event ----
        elif topic == "door/event" or topic == "door/events":
            try:
                parsed = json.loads(payload)
                event_type = parsed.get("eventType") or parsed.get("event_type") or parsed.get("event") or parsed.get("type") or "event"
                trigger = parsed.get("trigger") or parsed.get("trigger_source") or "sensor"
                doc = {
                    "timestamp": timestamp,
                    "event_type": event_type,
                    "status": parsed.get("status", current_status.get("status")),
                    "trigger_source": trigger,
                    "details": parsed
                }
            except Exception:
                event_type = payload.split(":")[0] if ":" in payload else payload
                doc = {
                    "timestamp": timestamp,
                    "event_type": event_type,
                    "status": current_status.get("status"),
                    "trigger_source": "manual" if "manual" in payload else ("sensor" if "sensor" in payload else "auto"),
                    "details": payload
                }
            events_collection.insert_one(doc)
            print(f"✓ Event logged: {event_type}")

        # ---- door/error ----
        elif topic == "door/error":
            error_doc = {
                "timestamp": timestamp,
                "event_type": "error",
                "status": current_status.get("status"),
                "error_message": payload
            }
            events_collection.insert_one(error_doc)

        # Broadcast update
        await broadcast_update({
            "type": "mqtt_update",
            "topic": topic,
            "payload": payload,
            "current_status": current_status
        })
    except Exception as e:
        print("Error in process_mqtt_message:", e)
        traceback.print_exc()

def update_daily_stats(event_type, timestamp):
    date_str = timestamp.strftime("%Y-%m-%d")
    update_fields = {"$inc": {}}
    if event_type == "door_opened":
        update_fields["$inc"]["open_count"] = 1
    elif event_type == "door_closed":
        update_fields["$inc"]["close_count"] = 1
    if update_fields["$inc"]:
        stats_collection.update_one({"_id": date_str}, update_fields, upsert=True)

async def broadcast_update(message: dict):
    disconnected = []
    for connection in list(active_connections):
        try:
            await connection.send_json(message)
        except Exception:
            disconnected.append(connection)
    for conn in disconnected:
        try:
            active_connections.remove(conn)
        except ValueError:
            pass

@app.on_event("startup")
async def startup_event():
    global event_loop, mqtt_connected

    event_loop = asyncio.get_running_loop()
    print("Running event loop captured.")

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        print("MQTT client started")
    except Exception as e:
        print(f"Error connecting to MQTT: {e}")
        traceback.print_exc()

    status_doc = status_collection.find_one({"_id": "current"})
    if status_doc:
        current_status.update({
            "status": status_doc.get("status", "unknown"),
            "distance": status_doc.get("sensor_distance", 0),
            "last_updated": status_doc.get("last_updated", datetime.utcnow()).isoformat()
        })

@app.on_event("shutdown")
async def shutdown_event():
    try:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except Exception:
        pass
    try:
        mongo_client.close()
    except Exception:
        pass

# API Endpoints
@app.get("/")
async def root():
    return {"message": "Door Control API", "status": "running"}

@app.get("/api/status")
async def get_status():
    return JSONResponse(content=current_status)

@app.post("/api/control")
async def control_door(command: ControlCommand):
    valid_commands = ["open", "close", "stop", "auto_on", "auto_off"]

    if command.command not in valid_commands:
        raise HTTPException(status_code=400, detail="Invalid command")

    if not mqtt_connected:
        raise HTTPException(status_code=503, detail="MQTT not connected")

    try:
        mqtt_client.publish("door/command", command.command)
    except Exception as e:
        print("MQTT publish error:", e)
        raise HTTPException(status_code=500, detail="Failed to publish MQTT command")

    event_doc = {
        "timestamp": datetime.utcnow(),
        "event_type": f"manual_{command.command}",
        "status": current_status.get("status"),
        "trigger_source": "web",
        "command": command.command
    }
    events_collection.insert_one(event_doc)

    return {"success": True, "command": command.command}

@app.get("/api/history")
async def get_history(limit: int = 50, skip: int = 0):
    events = list(
        events_collection.find(
            {},
            {"_id": 0}
        ).sort("timestamp", DESCENDING).skip(skip).limit(limit)
    )

    for event in events:
        if "timestamp" in event and isinstance(event["timestamp"], datetime):
            event["timestamp"] = event["timestamp"].isoformat()

    total = events_collection.count_documents({})

    return {
        "events": events,
        "total": total,
        "limit": limit,
        "skip": skip
    }

@app.get("/api/statistics")
async def get_statistics(days: int = 7):
    start_date = datetime.utcnow() - timedelta(days=days)

    daily_stats = list(
        stats_collection.find(
            {"_id": {"$gte": start_date.strftime("%Y-%m-%d")}},
            {"_id": 1, "open_count": 1, "close_count": 1}
        ).sort("_id", DESCENDING)
    )

    total_opens = sum(stat.get("open_count", 0) for stat in daily_stats)
    total_closes = sum(stat.get("close_count", 0) for stat in daily_stats)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    today_stats = stats_collection.find_one({"_id": today})

    return {
        "period_days": days,
        "total_opens": total_opens,
        "total_closes": total_closes,
        "today_opens": today_stats.get("open_count", 0) if today_stats else 0,
        "today_closes": today_stats.get("close_count", 0) if today_stats else 0,
        "daily_stats": daily_stats
    }

@app.get("/api/events/recent")
async def get_recent_events(limit: int = 10):
    events = list(
        events_collection.find(
            {},
            {"_id": 0}
        ).sort("timestamp", DESCENDING).limit(limit)
    )

    for event in events:
        if "timestamp" in event and isinstance(event["timestamp"], datetime):
            event["timestamp"] = event["timestamp"].isoformat()

    return {"events": events}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)

    await websocket.send_json({
        "type": "initial_status",
        "current_status": current_status
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                if isinstance(parsed, dict) and parsed.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except Exception:
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        try:
            active_connections.remove(websocket)
        except ValueError:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)