"""
database.py - MongoDB connection (Simple version)
"""
from pymongo import MongoClient, DESCENDING
from config import MONGODB_URI

# MongoDB connection
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client["door_system"]

# Collections
door_sessions = db["door_sessions"]
door_stats_hourly = db["door_stats_hourly"]
door_status = db["door_status"]
door_events = db["door_events"]

def init_indexes():
    """Create database indexes"""
    door_sessions.create_index([("open_time", DESCENDING)])
    door_sessions.create_index([("date", DESCENDING)])
    door_stats_hourly.create_index([("datetime", DESCENDING)])
    door_events.create_index([("timestamp", DESCENDING)])
    print("✓ Database indexes created")

def close_connection():
    """Close MongoDB connection"""
    try:
        mongo_client.close()
        print("MongoDB connection closed")
    except Exception as e:
        print(f"Error closing MongoDB: {e}")