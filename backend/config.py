"""
config.py - Configuration (Simple version - No Auth)
"""
import os
from datetime import timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")

# MQTT Broker (Shiftr.io)
MQTT_BROKER = os.getenv("MQTT_BROKER", "myiot-mqtt.cloud.shiftr.io")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "myiot-mqtt")
MQTT_PASS = os.getenv("MQTT_PASS", "IiIJDp6KZ66NMlJz")

# Timezone Vietnam (UTC+7)
VIETNAM_TZ = timezone(timedelta(hours=7))

# MQTT Topics
TOPIC_STATUS = "door/status"
TOPIC_COMMAND = "door/command"
TOPIC_SENSOR = "door/sensor"
TOPIC_EVENT = "door/event"