"""
utils.py - Utility functions
"""
from datetime import datetime, timezone
from config import VIETNAM_TZ

def get_vietnam_time():
    """Get current time in Vietnam timezone"""
    return datetime.now(VIETNAM_TZ)

def get_vietnam_date_str():
    """Get current date string in Vietnam timezone (YYYY-MM-DD)"""
    return get_vietnam_time().strftime("%Y-%m-%d")

def format_datetime_for_response(dt):
    """Format datetime for API response"""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc).astimezone(VIETNAM_TZ)
        return dt.isoformat()
    return dt