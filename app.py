# app.py
import os
import time
import logging
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from twilio.rest import Client
from dotenv import load_dotenv
import warnings
from datetime import timedelta   # ← make sure this import exists at the top

load_dotenv()

# ---------- CONFIG ----------
DATABASE_FILE = os.getenv("DATABASE_FILE", "bins.db")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
YOUR_PHONE_NUMBER = os.getenv("YOUR_PHONE_NUMBER")
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "60"))

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("smart-bin")

# Flask + SQLAlchemy
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DATABASE_FILE}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ---------- MODELS ----------
class Bin(db.Model):
    __tablename__ = "bins"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(32), unique=True, nullable=False)
    last_alert_ts = db.Column(db.Float, default=0.0)
    latest_level = db.Column(db.Integer, default=0)
    # NOTE: no threshold column here (we are not adding per-bin threshold feature)

class Reading(db.Model):
    __tablename__ = "readings"
    id = db.Column(db.Integer, primary_key=True)
    bin_name = db.Column(db.String(32), nullable=False)
    level = db.Column(db.Integer, nullable=False)
    ts = db.Column(db.DateTime, default=datetime.utcnow)

# Feature 3: Settings table (simulator pause)
class Setting(db.Model):
    __tablename__ = "settings"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False)
    value = db.Column(db.String(256), nullable=False)

# Feature 4: Admin action log
class ActionLog(db.Model):
    __tablename__ = "action_logs"
    id = db.Column(db.Integer, primary_key=True)
    action_type = db.Column(db.String(64), nullable=False)  # e.g., "simulator_paused", "manual_alert", "config_saved"
    detail = db.Column(db.String(512), nullable=True)
    ts = db.Column(db.DateTime, default=datetime.utcnow)

# ---------- HELPERS ----------
def safe_send_sms(bin_name, level):
    """Safe Twilio wrapper; never raises if creds missing."""
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, YOUR_PHONE_NUMBER]):
        logger.warning("Twilio not configured; skipping SMS send.")
        return {"sent": False, "error": "twilio_not_configured"}

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        body = f"Alert: {bin_name.capitalize()} Bin is {level}% full. Please empty it soon."
        message = client.messages.create(body=body, from_=TWILIO_PHONE_NUMBER, to=YOUR_PHONE_NUMBER)
        logger.info("SMS sent SID=%s", message.sid)
        return {"sent": True, "sid": message.sid}
    except Exception as e:
        logger.exception("Failed to send SMS")
        return {"sent": False, "error": str(e)}

def init_bins_if_needed():
    """Create default bins (idempotent)."""
    for name in ["yellow", "green", "blue"]:
        if not Bin.query.filter_by(name=name).first():
            db.session.add(Bin(name=name))
    db.session.commit()

def get_setting(key, default=None):
    s = Setting.query.filter_by(key=key).first()
    return s.value if s else default

def set_setting(key, value):
    s = Setting.query.filter_by(key=key).first()
    if not s:
        s = Setting(key=key, value=str(value))
        db.session.add(s)
    else:
        s.value = str(value)
    db.session.commit()

def log_action(action_type, detail=None):
    try:
        db.session.add(ActionLog(action_type=action_type, detail=detail))
        db.session.commit()
        logger.info("Action logged: %s %s", action_type, detail)
    except Exception:
        logger.exception("Failed to log action")

# ---------- ROUTES ----------
@app.route("/")
def home():
    return render_template("index.html")


@app.route('/history')
def history_page():
    # Fetch last 200 readings from DB
    readings = Reading.query.order_by(Reading.ts.desc()).limit(200).all()

    # Convert UTC → IST inside Python
    readings_for_template = []
    for r in readings:
        ist_ts = (r.ts + timedelta(hours=5, minutes=30))
        readings_for_template.append({
            "bin_name": r.bin_name,
            "level": r.level,
            "ts_ist": ist_ts.strftime("%Y-%m-%d %H:%M:%S")
        })

    return render_template("history.html", readings=readings_for_template)


@app.route("/levels", methods=["GET"])
def get_levels():
    bins = Bin.query.all()
    return jsonify({b.name: b.latest_level for b in bins})

@app.route("/update_level/<bin_color>", methods=["POST"])
def update_level(bin_color):
    if bin_color not in ["yellow", "green", "blue"]:
        return jsonify({"error": "invalid bin"}), 400

    data = request.get_json(force=True, silent=True) or {}
    level = data.get("level")
    if level is None:
        level = data.get("value") or 0
    try:
        level = int(level)
    except Exception:
        level = 0

    b = Bin.query.filter_by(name=bin_color).first()
    if not b:
        b = Bin(name=bin_color, latest_level=level)
        db.session.add(b)

    b.latest_level = level
    db.session.add(Reading(bin_name=bin_color, level=level))
    db.session.commit()

    # basic alert logic (keeps previous behaviour — 80% hardcoded here; change if needed)
    alert_sent = False
    if level >= 80:
        now_ts = time.time()
        if now_ts - (b.last_alert_ts or 0) >= ALERT_COOLDOWN_SECONDS:
            res = safe_send_sms(bin_color, level)
            if res.get("sent"):
                b.last_alert_ts = now_ts
                db.session.commit()
                alert_sent = True
                log_action("auto_alert_sent", f"{bin_color} level={level} sid={res.get('sid')}")
            else:
                log_action("auto_alert_failed", f"{bin_color} level={level} error={res.get('error')}")

    return jsonify({
        "status": "success",
        "bin": bin_color,
        "level": level,
        "alert_sent": alert_sent
    })

@app.route("/readings/<bin_color>", methods=["GET"])
def readings(bin_color):
    if bin_color not in ["yellow", "green", "blue"]:
        return jsonify({"error": "invalid bin"}), 400
    last_n = int(request.args.get("n", 100))
    q = Reading.query.filter_by(bin_name=bin_color).order_by(Reading.ts.desc()).limit(last_n).all()
    out = [{"level": r.level, "ts": r.ts.isoformat()} for r in reversed(q)]
    return jsonify(out)

# ---------- CONFIG (Feature 3) ----------
@app.route("/config", methods=["GET"])
def get_config():
    simulator_paused = get_setting("simulator_paused", "false") == "true"
    return jsonify({"simulator_paused": simulator_paused})

@app.route("/config", methods=["PATCH"])
def patch_config():
    data = request.get_json(force=True, silent=True) or {}
    changed = []
    if "simulator_paused" in data:
        val = bool(data["simulator_paused"])
        set_setting("simulator_paused", str(val).lower())
        changed.append(f"simulator_paused={val}")
        log_action("simulator_paused_toggled", f"paused={val}")
    return jsonify({"status": "ok", "simulator_paused": get_setting("simulator_paused", "false") == "true", "changed": changed})

# ---------- ACTIONS (Feature 4) ----------
@app.route("/actions", methods=["GET"])
def get_actions():
    limit = int(request.args.get("n", 50))
    acts = ActionLog.query.order_by(ActionLog.ts.desc()).limit(limit).all()
    out = [{"action": a.action_type, "detail": a.detail, "ts": a.ts.isoformat()} for a in acts]
    return jsonify(out)

@app.route("/trigger_alert/<bin_color>", methods=["POST"])
def trigger_alert(bin_color):
    if bin_color not in ["yellow", "green", "blue"]:
        return jsonify({"error": "invalid bin"}), 400
    b = Bin.query.filter_by(name=bin_color).first()
    if not b:
        return jsonify({"error": "bin not found"}), 404
    res = safe_send_sms(bin_color, b.latest_level)
    if res.get("sent"):
        b.last_alert_ts = time.time()
        db.session.commit()
        log_action("manual_alert_sent", f"{bin_color} level={b.latest_level} sid={res.get('sid')}")
        return jsonify({"status": "alert_sent", "sid": res.get("sid")})
    else:
        log_action("manual_alert_failed", f"{bin_color} level={b.latest_level} error={res.get('error')}")
        return jsonify({"status": "alert_failed", "error": res.get("error")}), 500

# ---------- CLI helper ----------
@app.cli.command("initdb")
def initdb():
    db.create_all()
    init_bins_if_needed()
    print("DB initialized and bins created (if not existing).")

# ---------- RUN ----------
if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    with app.app_context():
        # create tables safely (idempotent)
        db.create_all()
        init_bins_if_needed()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
