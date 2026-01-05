A Flask-based smart waste management system that monitors garbage bin fill levels in real time and sends automatic SMS alerts when bins are about to overflow.
This project demonstrates the integration of backend APIs, real-time dashboards, IoT simulation, and cloud-based messaging.


System Architecture
Sensor Simulator (Python)
        |
        |  POST (JSON)
        v
Flask Backend (REST API)
        |
        |  /levels (GET)
        v
Web Dashboard (UI)
        |
        |  Alert Trigger (≥80%)
        v
Twilio SMS Service
