"""
mqtt_handler.py - MQTT client và message handling
"""
import asyncio
import json
import traceback
import paho.mqtt.client as mqtt
from typing import Optional

from config import MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, TOPIC_STATUS, TOPIC_SENSOR, TOPIC_EVENT
from database import door_status, door_sessions, door_events, door_stats_hourly
from utils import get_vietnam_time

# MQTT Client
mqtt_client = mqtt.Client()
mqtt_connected = False
event_loop: Optional[asyncio.AbstractEventLoop] = None

# Current status cache
current_status = {
    "status": "unknown",
    "distance": 0,
    "last_updated": get_vietnam_time().isoformat(),
    "auto_mode": True
}

# Session tracking
current_session = {
    "open_time": None,
    "open_distance": None,
    "is_manual": False
}

# ==================== MQTT CALLBACKS ====================
def on_mqtt_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        print("Connected to MQTT Broker!")
        mqtt_connected = True
        client.subscribe("door/#")
        print("  Subscribed to: door/#")
    else:
        print(f"Failed to connect to MQTT, return code {rc}")
        mqtt_connected = False

def on_mqtt_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode(errors="ignore")
    print(f"MQTT [{topic}]: {payload}")

    global event_loop
    if event_loop is None:
        print("Event loop not ready yet - skipping MQTT message")
        return

    try:
        asyncio.run_coroutine_threadsafe(process_mqtt_message(topic, payload), event_loop)
    except Exception as e:
        print("Error scheduling MQTT message processing:", e)
        traceback.print_exc()

# ==================== MESSAGE PROCESSING ====================
async def process_mqtt_message(topic, payload):
    global current_status, current_session
    timestamp = get_vietnam_time()

    try:
        # ---- door/status ----
        if topic == TOPIC_STATUS:
            try:
                parsed = json.loads(payload)
                status_val = parsed.get("status", "").lower()
                distance = parsed.get("distance")
            except Exception:
                status_val = payload.strip().lower()
                distance = None

            old_status = current_status.get("status")
            current_status["status"] = status_val
            current_status["last_updated"] = timestamp.isoformat()
            if distance is not None:
                current_status["distance"] = distance

            door_status.update_one(
                {"_id": "current"}, 
                {
                    "$set": {
                        "status": status_val,
                        "distance": distance,
                        "last_updated": timestamp
                    }
                }, 
                upsert=True
            )

            # Session tracking
            if status_val == "open" and old_status != "open":
                current_session["open_time"] = timestamp
                current_session["open_distance"] = distance or 0
                print(f"Session started at {timestamp}")

            elif status_val == "closed" and old_status in ["open", "closing"]:
                if current_session["open_time"]:
                    open_time = current_session["open_time"]
                    duration = (timestamp - open_time).total_seconds()
                    
                    session_doc = {
                        "open_time": open_time,
                        "close_time": timestamp,
                        "duration_seconds": duration,
                        "date": open_time.strftime("%Y-%m-%d"),
                        "hour": open_time.hour,
                        "is_manual": current_session.get("is_manual", False),
                        "open_distance": current_session.get("open_distance", 0)
                    }
                    door_sessions.insert_one(session_doc)
                    print(f"✓ Session saved: {duration:.1f}s")
                    
                    update_hourly_stats(open_time, duration)
                    
                    current_session = {
                        "open_time": None,
                        "open_distance": None,
                        "is_manual": False
                    }

        # ---- door/sensor ----
        elif topic == TOPIC_SENSOR:
            try:
                distance = int(payload)
                current_status["distance"] = distance
                current_status["last_updated"] = timestamp.isoformat()
                
                door_status.update_one(
                    {"_id": "current"},
                    {"$set": {"distance": distance, "last_updated": timestamp}},
                    upsert=True
                )
            except Exception:
                pass

        # ---- door/event ----
        elif topic == TOPIC_EVENT:
            try:
                parsed = json.loads(payload)
                event_type = parsed.get("type", "event")
                
                if event_type in ["sensor_detected", "manual_open", "manual_close", "error"]:
                    event_doc = {
                        "timestamp": timestamp,
                        "type": event_type,
                        "state": parsed.get("state"),
                        "distance": parsed.get("distance"),
                        "details": parsed
                    }
                    door_events.insert_one(event_doc)
            except Exception:
                pass

        # Broadcast to WebSocket clients
        from websocket_manager import broadcast_update
        await broadcast_update({
            "type": "mqtt_update",
            "current_status": current_status
        })
        
    except Exception as e:
        print("Error in process_mqtt_message:", e)
        traceback.print_exc()

def update_hourly_stats(open_time, duration):
    """Update hourly statistics for pattern analysis"""
    hour_key = open_time.strftime("%Y-%m-%d-%H")
    
    door_stats_hourly.update_one(
        {"_id": hour_key},
        {
            "$inc": {
                "open_count": 1,
                "total_duration": duration
            },
            "$set": {
                "date": open_time.strftime("%Y-%m-%d"),
                "hour": open_time.hour,
                "datetime": open_time.replace(minute=0, second=0, microsecond=0)
            }
        },
        upsert=True
    )

# ==================== MQTT CONTROL ====================
def start_mqtt(loop: asyncio.AbstractEventLoop):
    """Start MQTT client"""
    global event_loop, mqtt_connected
    event_loop = loop
    
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message
    
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        print("MQTT client started")
    except Exception as e:
        print(f"Error connecting to MQTT: {e}")
        traceback.print_exc()

def stop_mqtt():
    """Stop MQTT client"""
    try:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print("MQTT client stopped")
    except Exception as e:
        print(f"Error stopping MQTT: {e}")

def publish_command(command: str):
    """Publish command to MQTT"""
    if not mqtt_connected:
        raise Exception("MQTT not connected")
    
    try:
        mqtt_client.publish("door/command", command)
        return True
    except Exception as e:
        print("MQTT publish error:", e)
        raise

# ==================== STATUS ====================
def get_current_status():
    """Get current door status"""
    return current_status

def set_manual_operation(is_manual: bool):
    """Mark current operation as manual"""
    global current_session
    current_session["is_manual"] = is_manual