import hmac
import math
import os, secrets, smtplib, datetime
from getpass import getpass
import click
from pathlib import Path
from dotenv import load_dotenv

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text, or_

# Load this project's configuration regardless of the shell's current directory.
load_dotenv(Path(__file__).resolve().with_name('.env'))

app = Flask(__name__)
DEFAULT_PRESET_LIMIT = 500.0
DEFAULT_APPLIANCE_CONFIGS = {
    "acu": {"display_name": "ACU Unit", "kw_limit": 10.0},
    "outlet": {"display_name": "3-Gang Outlet", "kw_limit": 5.0},
    "lighting": {"display_name": "Lighting Circuit", "kw_limit": 2.0},
}
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///users.db')
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE') == '1',
    MAX_CONTENT_LENGTH=32 * 1024,
)


def utcnow_naive():
    """Return UTC time as naive datetime for compatibility with existing SQLite rows."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


db = SQLAlchemy(app)
login_manager = LoginManager(app)
# Pylance may complain about assignment types here; ignore the checker for this runtime assignment
login_manager.login_view = "login"  # type: ignore[assignment]

# ------------------ MODELS ------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(100))
    birthday = db.Column(db.String(20))
    age = db.Column(db.Integer)
    address = db.Column(db.String(200))
    verified = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    def __init__(self, email: str, name: str | None = None, birthday: str | None = None, age: int | str | None = None, address: str | None = None, **kwargs):
        # Provide an explicit constructor so static checkers (Pylance) recognize these parameters
        super().__init__(**kwargs)
        self.email = email
        self.name = name
        self.birthday = birthday
        # allow None for age; accept string or int and coerce to int when possible
        coerced_age = None
        if age is not None and age != "":
            try:
                coerced_age = int(age)
            except Exception:
                coerced_age = None
        self.age = coerced_age
        self.address = address

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Log(db.Model):
    __tablename__ = "logs"
    __table_args__ = (db.Index("ix_logs_date_recorded", "date_recorded"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    voltage = db.Column(db.Float)
    current = db.Column(db.Float)
    power = db.Column(db.Float)
    slave_id = db.Column(db.Integer)
    energy = db.Column(db.Float)
    date_recorded = db.Column(db.DateTime, default=utcnow_naive)

    user = db.relationship("User", backref=db.backref("logs", lazy=True))


class ApplianceConfig(db.Model):
    __tablename__ = "appliance_configs"

    id = db.Column(db.Integer, primary_key=True)
    appliance_key = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    kw_limit = db.Column(db.Float, default=10.0, nullable=False)
    is_tripped = db.Column(db.Boolean, default=False)
    requested_state = db.Column(db.String(3), nullable=False, default='OFF')
    updated_at = db.Column(db.DateTime, default=utcnow_naive)


class PowerEvent(db.Model):
    __tablename__ = "power_events"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50), nullable=False)
    value_watts = db.Column(db.Float, default=0.0)
    preset_limit = db.Column(db.Float, default=0.0)
    message = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=utcnow_naive)


class VerificationCode(db.Model):
    __tablename__ = "verification_codes"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), nullable=False, index=True)
    purpose = db.Column(db.String(20), nullable=False)
    code_hash = db.Column(db.String(256), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)


class SystemSettings(db.Model):
    __tablename__ = 'system_settings'
    id = db.Column(db.Integer, primary_key=True)
    preset_limit_watts = db.Column(db.Float, nullable=False, default=DEFAULT_PRESET_LIMIT)


def get_preset_limit():
    settings = db.session.get(SystemSettings, 1)
    if settings is None:
        settings = SystemSettings(id=1, preset_limit_watts=DEFAULT_PRESET_LIMIT)
        db.session.add(settings)
        db.session.commit()
    try:
        value = float(settings.preset_limit_watts)
    except (TypeError, ValueError):
        value = float('nan')
    if not math.isfinite(value) or value <= 0:
        settings.preset_limit_watts = DEFAULT_PRESET_LIMIT
        db.session.commit()
        return DEFAULT_PRESET_LIMIT
    return value

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def normalize_email(email):
    return email.strip().lower()


def convert_watts_to_kw(raw_watts):
    try:
        return float(raw_watts or 0.0) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def convert_wh_to_kwh(raw_wh):
    try:
        return float(raw_wh or 0.0) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def ensure_appliance_configs():
    changed = False
    for appliance_key, config in DEFAULT_APPLIANCE_CONFIGS.items():
        existing = ApplianceConfig.query.filter_by(appliance_key=appliance_key).first()
        if existing is None:
            db.session.add(ApplianceConfig(
                appliance_key=appliance_key,
                display_name=config["display_name"],
                kw_limit=float(config["kw_limit"]),
                is_tripped=False,
                requested_state='OFF',
            ))
            changed = True
        else:
            if not existing.display_name:
                existing.display_name = config["display_name"]
                changed = True
            if existing.kw_limit is None:
                existing.kw_limit = float(config["kw_limit"])
                changed = True
            if not existing.requested_state:
                existing.requested_state = 'OFF'
                changed = True
            try:
                existing_limit = float(existing.kw_limit)
            except (TypeError, ValueError):
                existing_limit = float('nan')
            if not math.isfinite(existing_limit) or existing_limit <= 0:
                existing.kw_limit = float(config['kw_limit'])
                changed = True
            if existing.requested_state not in ('ON', 'OFF'):
                existing.requested_state = 'OFF'
                changed = True
    if changed:
        db.session.commit()


def get_appliance_configs():
    ensure_appliance_configs()
    entries = [ApplianceConfig.query.filter_by(appliance_key=key).first() for key in DEFAULT_APPLIANCE_CONFIGS]
    payload = {}
    for entry in entries:
        payload[entry.appliance_key] = {
            "display_name": entry.display_name,
            "kw_limit": float(entry.kw_limit or 0.0),
            "is_tripped": bool(entry.is_tripped),
        }
    for key, defaults in DEFAULT_APPLIANCE_CONFIGS.items():
        payload.setdefault(key, {
            "display_name": defaults["display_name"],
            "kw_limit": float(defaults["kw_limit"]),
            "is_tripped": False,
        })
    return payload


def normalize_appliance_states(states):
    if not isinstance(states, list) or len(states) != 3:
        return None

    cleaned = []
    for item in states:
        value = str(item).strip().upper()
        if value not in ("ON", "OFF"):
            return None
        cleaned.append(value)

    return cleaned[:3]


def ordered_appliance_configs():
    ensure_appliance_configs()
    return [ApplianceConfig.query.filter_by(appliance_key=key).first() for key in DEFAULT_APPLIANCE_CONFIGS]


def device_token_valid():
    expected = os.environ.get('DEVICE_API_TOKEN', '')
    if app.testing:
        return True
    supplied = request.headers.get('Authorization', '')
    return bool(expected) and hmac.compare_digest(supplied, f'Bearer {expected}')


def require_device_token():
    if not device_token_valid():
        return jsonify({'error': 'device authentication required'}), 401
    return None


def csrf_valid():
    expected = session.get('csrf_token', '')
    supplied = request.headers.get('X-CSRFToken', '') or request.form.get('csrf_token', '')
    return bool(expected) and hmac.compare_digest(expected, supplied)


@app.context_processor
def inject_csrf_token():
    session.setdefault('csrf_token', secrets.token_urlsafe(32))
    return {'csrf_token': session['csrf_token']}


@app.before_request
def protect_browser_posts():
    if request.method != 'POST':
        return None
    device_write_paths = {'/api/data', '/api/device-trip', '/api/power-events', '/api/config'}
    if request.path in device_write_paths:
        return None  # These routes validate the Pi bearer token themselves.
    if not csrf_valid():
        if request.path.startswith('/api/'):
            return jsonify({'error': 'valid CSRF token required'}), 403
        return 'Invalid CSRF token', 403
    return None


def migrate_schema():
    """Add columns introduced after the original SQLite schema was created."""
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    if 'logs' in tables:
        columns = {column['name'] for column in inspector.get_columns('logs')}
        if 'slave_id' not in columns:
            db.session.execute(text('ALTER TABLE logs ADD COLUMN slave_id INTEGER'))
        if 'energy' not in columns:
            db.session.execute(text('ALTER TABLE logs ADD COLUMN energy FLOAT'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_logs_date_recorded ON logs(date_recorded)'))
    if 'users' in tables:
        columns = {column['name'] for column in inspector.get_columns('users')}
        if 'is_admin' not in columns:
            db.session.execute(text('ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 1'))
    if 'appliance_configs' in tables:
        columns = {column['name'] for column in inspector.get_columns('appliance_configs')}
        if 'requested_state' not in columns:
            db.session.execute(text("ALTER TABLE appliance_configs ADD COLUMN requested_state VARCHAR(3) NOT NULL DEFAULT 'OFF'"))
    db.session.commit()


@app.cli.command('init-db')
def init_db_command():
    """Create tables and safely migrate the original SQLite schema."""
    migrate_legacy_tables()
    db.create_all()
    migrate_schema()
    ensure_appliance_configs()
    get_preset_limit()
    print('Database initialized.')


@app.cli.command('create-test-admin')
def create_test_admin_command():
    """Create or reset a verified administrator for local website testing."""
    with app.app_context():
        migrate_legacy_tables()
        db.create_all()
        migrate_schema()
        email = normalize_email(input('Admin email: '))
        if not email or '@' not in email:
            raise click.ClickException('Enter a valid email address.')
        password = getpass('Password (at least 10 characters): ')
        if len(password) < 10:
            raise click.ClickException('Password must be at least 10 characters.')

        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(email=email, is_admin=True)
            db.session.add(user)
        user.set_password(password)
        user.verified = True
        user.is_admin = True
        db.session.commit()
        print(f'Verified test administrator ready: {email}')


def migrate_legacy_tables():
    with app.app_context():
        tables = inspect(db.engine).get_table_names()
        if "user" in tables and "users" not in tables:
            db.session.execute(text("ALTER TABLE user RENAME TO users"))
        if "log" in tables and "logs" not in tables:
            db.session.execute(text("ALTER TABLE log RENAME TO logs"))
        if "user" in tables or "log" in tables:
            db.session.commit()


def send_verification_email(to_email, code):
    username = os.environ.get("MAIL_USERNAME")
    password = os.environ.get("MAIL_PASSWORD")

    if not username or not password:
        return False

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()
            server.login(username, password)
            server.sendmail(username, to_email,
                f"Subject: Verify your account\n\nYour code is {code}")
        return True
    except Exception:
        return False


def send_reset_email(to_email, code):
    username = os.environ.get("MAIL_USERNAME")
    password = os.environ.get("MAIL_PASSWORD")

    if not username or not password:
        return False

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()
            server.login(username, password)
            server.sendmail(username, to_email,
                f"Subject: Password Reset\n\nYour reset code is {code}")
        return True
    except Exception:
        return False


def issue_verification_code(email, purpose):
    code = f"{secrets.randbelow(1_000_000):06d}"
    item = VerificationCode.query.filter_by(email=email, purpose=purpose).first()
    if item is None:
        item = VerificationCode(email=email, purpose=purpose, code_hash="", expires_at=utcnow_naive())
        db.session.add(item)
    item.code_hash = generate_password_hash(code)
    item.expires_at = utcnow_naive() + datetime.timedelta(minutes=10)
    item.attempts = 0
    db.session.commit()
    return code


def consume_verification_code(email, purpose, code):
    item = VerificationCode.query.filter_by(email=email, purpose=purpose).first()
    if item is None or item.attempts >= 5 or item.expires_at < utcnow_naive():
        return False
    item.attempts += 1
    valid = check_password_hash(item.code_hash, str(code))
    if valid:
        db.session.delete(item)
    db.session.commit()
    return valid


def demo_codes_enabled():
    """Allow demo codes explicitly, or for a no-mail loopback development session."""
    configured = os.environ.get("ALLOW_DEMO_CODES")
    if configured is not None:
        return configured.strip() == "1"
    if app.testing:
        return True
    mail_is_configured = bool(os.environ.get("MAIL_USERNAME") and os.environ.get("MAIL_PASSWORD"))
    return not mail_is_configured and request.remote_addr in {"127.0.0.1", "::1"}

# ------------------ ROUTES ------------------
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        name = request.form.get("name", "").strip()
        birthday = request.form.get("birthday", "")
        age = request.form.get("age", "")
        address = request.form.get("address", "")

        if not email or not name:
            flash("Email and name are required.")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match. Please re-enter both fields.")
            return render_template("register.html")
        if len(password) < 10:
            flash("Password must be at least 10 characters.")
            return render_template("register.html")

        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists. Please log in or reset your password.")
            return redirect(url_for("login"))

        is_first_user = User.query.count() == 0
        user = User(email=email, name=name, birthday=birthday, age=age, address=address, is_admin=is_first_user)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        code = issue_verification_code(email, "verify")
        session["pending_email"] = email

        email_sent = send_verification_email(email, code)
        if not email_sent:
            if demo_codes_enabled():
                flash(f"Demo verification code: {code}.")
            else:
                flash("Verification email could not be sent. Configure mail and register again.")
                VerificationCode.query.filter_by(email=email, purpose="verify").delete()
                db.session.delete(user)
                db.session.commit()
                session.pop("pending_email", None)
                return redirect(url_for("register"))
            return redirect(url_for("verify"))

        flash("Verification code sent to your email.")
        return redirect(url_for("verify"))
    return render_template("register.html")

@app.route("/verify", methods=["GET","POST"])
def verify():
    if request.method == "POST":
        code = request.form.get("code", "")
        email = session.get("pending_email")
        user = User.query.filter_by(email=email).first() if email else None
        if email and consume_verification_code(email, "verify", code):
            if user:
                user.verified = True
                db.session.commit()
                session.pop("pending_email", None)
                flash("Account verified! You can now login.")
            else:
                flash("No pending user found for verification.")
            return redirect(url_for("login"))
        else:
            flash("Invalid code.")
    return render_template("verify.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and hasattr(user, "check_password") and user.check_password(password) and getattr(user, "verified", False):
            login_user(user)
            return redirect(url_for("dashboard"))
        elif user and not user.verified:
            flash("Account not verified. Check your email, or enable ALLOW_DEMO_CODES=1 for local registration testing.")
        else:
            flash("Invalid credentials or account not verified.")
    return render_template("login.html")

@app.route("/forgot", methods=["GET","POST"])
def forgot():
    if request.method == "POST":
        session.pop("reset_email", None)
        email = normalize_email(request.form.get("email", ""))
        user = User.query.filter_by(email=email).first()
        if user:
            code = issue_verification_code(email, "reset")
            session["reset_email"] = email
            email_sent = send_reset_email(email, code)
            if not email_sent:
                if demo_codes_enabled():
                    flash(f"Demo reset code: {code}.")
                else:
                    VerificationCode.query.filter_by(email=email, purpose="reset").delete()
                    db.session.commit()
                    session.pop("reset_email", None)
                    flash("If the account exists, a reset code will be sent.")
            else:
                flash("If the account exists, a reset code will be sent.")
        else:
            flash("If the account exists, a reset code will be sent.")
        return redirect(url_for("reset"))
    return render_template("forgot.html")

@app.route("/reset", methods=["GET","POST"])
def reset():
    if request.method == "POST":
        code = request.form.get("code", "")
        new_password = request.form.get("password", "")
        if len(new_password) < 10 or new_password != request.form.get("confirm_password", ""):
            flash("Use a matching password of at least 10 characters.")
            return render_template("reset.html")
        email = session.get("reset_email")
        if email and consume_verification_code(email, "reset", code):
            user = User.query.filter_by(email=email).first() if email else None
            if user:
                user.set_password(new_password)
                db.session.commit()
                session.pop("reset_email", None)
                flash("Password reset successful.")
            else:
                flash("Password reset could not be completed.")
            return redirect(url_for("login"))
        else:
            flash("Invalid reset code.")
    return render_template("reset.html")

@app.route("/dashboard")
@login_required
def dashboard():
    logs = Log.query.filter(or_(Log.user_id == current_user.id, Log.user_id.is_(None))) \
        .order_by(Log.date_recorded.desc()).all()
    display_name = (current_user.name or current_user.email or "Admin").strip()
    if not display_name or display_name == "Admin":
        display_name = (current_user.email or "Admin").split("@", 1)[0].title()
    session.setdefault('csrf_token', secrets.token_urlsafe(32))
    states = [entry.requested_state for entry in ordered_appliance_configs()]
    return render_template(
        "dashboard.html",
        logs=logs,
        appliance_configs=get_appliance_configs(),
        appliance_states=states,
        preset_limit=get_preset_limit(),
        csrf_token=session['csrf_token'],
        can_control=bool(current_user.is_admin),
        user_display_name=display_name,
    )

@app.route("/api/control", methods=["GET", "POST"])
def api_control():
    if request.method == "POST":
        if not current_user.is_authenticated or not current_user.is_admin or not csrf_valid():
            return jsonify({"error": "authenticated session and CSRF token required"}), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "payload must be an object"}), 400
        states = payload.get("appliances") or payload.get("appliance_states")
        normalized = normalize_appliance_states(states)
        if normalized is None:
            return jsonify({"error": "appliances must be a list of ON/OFF values"}), 400

        entries = ordered_appliance_configs()
        for entry, state in zip(entries, normalized):
            entry.requested_state = state
            if state == 'ON':
                entry.is_tripped = False
            entry.updated_at = utcnow_naive()
        db.session.commit()
        names = [entry.display_name for entry in entries]

        return jsonify({
            "appliances": normalized,
            "tripped": [entry.is_tripped for entry in entries],
            "preset_limit": get_preset_limit(),
            "appliance_names": names,
            "status": "updated"
        })

    if not current_user.is_authenticated and not device_token_valid():
        return jsonify({'error': 'authentication required'}), 401
    ensure_appliance_configs()
    entries = ordered_appliance_configs()
    states = [entry.requested_state for entry in entries]
    return jsonify({
        "appliances": states,
        "tripped": [entry.is_tripped for entry in entries],
        "preset_limit": get_preset_limit(),
        "appliance_names": [entry.display_name for entry in entries]
    })


@app.route('/api/device-trip', methods=['POST'])
def api_device_trip():
    denied = require_device_token()
    if denied:
        return denied
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'payload must be an object'}), 400
    try:
        index = int(payload['index'])
        value = float(payload['value_watts'])
        limit = float(payload['limit_watts'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'index and watt values are required'}), 400
    entries = ordered_appliance_configs()
    if index not in range(3) or not math.isfinite(value) or not math.isfinite(limit):
        return jsonify({'error': 'invalid trip data'}), 400
    entry = entries[index]
    entry.requested_state = 'OFF'
    entry.is_tripped = True
    entry.updated_at = utcnow_naive()
    reason = str(payload.get('reason', 'power_limit'))[:50]
    message = f'{entry.display_name} cutoff: {reason}.'
    db.session.add(PowerEvent(event_type=reason, value_watts=value,
                              preset_limit=limit, message=message))
    db.session.commit()
    return jsonify({'status': 'tripped', 'appliances': [item.requested_state for item in entries]}), 200

@app.route("/api/data", methods=["POST"])
def api_data():
    """Accept a sensor batch from the Raspberry Pi and save usable readings."""
    denied = require_device_token()
    if denied:
        return denied
    payload = request.get_json(silent=True)
    readings = payload.get("readings") if isinstance(payload, dict) else payload
    if not isinstance(readings, list) or len(readings) > 3:
        return jsonify({"error": "request body must be a list of sensor readings"}), 400

    accepted = []
    seen_slaves = set()
    for reading in readings:
        if not isinstance(reading, dict):
            continue

        try:
            power = float(reading["power"])
            slave_id = int(reading["slave_id"])
        except (KeyError, TypeError, ValueError):
            # PZEM errors and incomplete samples contain None values; skip them.
            continue
        if not math.isfinite(power) or power < 0 or not 1 <= slave_id <= 247 or slave_id in seen_slaves:
            continue

        def optional_reading(name):
            try:
                value = float(reading[name])
            except (KeyError, TypeError, ValueError):
                return None
            return value if math.isfinite(value) and value >= 0 else None

        voltage = optional_reading('voltage')
        current = optional_reading('current')
        energy = optional_reading('energy')
        seen_slaves.add(slave_id)

        accepted.append(Log(
            voltage=voltage,
            current=current,
            power=power,
            slave_id=slave_id,
            energy=energy,
        ))

    if not accepted:
        return jsonify({"error": "no readings contained valid voltage, current, and power values", "accepted": 0}), 400

    try:
        retention_cutoff = utcnow_naive() - datetime.timedelta(days=30)
        Log.query.filter(Log.date_recorded < retention_cutoff).delete(synchronize_session=False)
        db.session.add_all(accepted)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to save incoming sensor readings")
        return jsonify({"error": "failed to save sensor readings"}), 500

    return jsonify({"status": "accepted", "accepted": len(accepted), "received": len(readings)}), 201


@app.route('/api/latest', methods=['GET'])
@login_required
def api_latest():
    latest = {}
    rows = Log.query.filter(Log.slave_id.isnot(None)).order_by(Log.date_recorded.desc()).limit(100).all()
    for row in rows:
        latest.setdefault(row.slave_id, {
            'slave_id': row.slave_id,
            'voltage': row.voltage,
            'current': row.current,
            'power': row.power,
            'energy': row.energy,
            'date_recorded': row.date_recorded.isoformat() if row.date_recorded else None,
        })
    return jsonify({'readings': list(latest.values())})


@app.route('/api/power-events', methods=['POST'])
def ingest_power_event():
    denied = require_device_token()
    if denied:
        return denied
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not payload.get('event_type'):
        return jsonify({'error': 'event_type is required'}), 400
    try:
        value = float(payload.get('value_watts', 0.0))
        limit = float(payload.get('preset_limit', 0.0))
    except (TypeError, ValueError):
        return jsonify({'error': 'event watt values must be numeric'}), 400
    if not math.isfinite(value) or not math.isfinite(limit):
        return jsonify({'error': 'event watt values must be finite'}), 400
    event = PowerEvent(
        event_type=str(payload['event_type'])[:50],
        value_watts=value,
        preset_limit=limit,
        message=str(payload.get('message', ''))[:255],
    )
    db.session.add(event)
    db.session.commit()
    return jsonify({'id': event.id, 'status': 'accepted'}), 201

@app.route("/api/appliance-config", methods=["GET", "POST"])
def api_appliance_config():
    if request.method == "POST":
        if not current_user.is_authenticated or not current_user.is_admin or not csrf_valid():
            return jsonify({"error": "authenticated session and CSRF token required"}), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "payload must be an object"}), 400

        for appliance_key, config in payload.items():
            if appliance_key not in DEFAULT_APPLIANCE_CONFIGS:
                return jsonify({"error": f"unknown appliance key: {appliance_key}"}), 400
            if not isinstance(config, dict):
                continue
            try:
                limit = float(config.get("kw_limit", DEFAULT_APPLIANCE_CONFIGS[appliance_key]["kw_limit"]))
            except (TypeError, ValueError):
                return jsonify({"error": "kw_limit must be a number"}), 400
            if not math.isfinite(limit) or limit <= 0:
                return jsonify({"error": "kw_limit must be a positive finite number"}), 400
            entry = ApplianceConfig.query.filter_by(appliance_key=appliance_key).first()
            if entry is None:
                entry = ApplianceConfig(
                    appliance_key=appliance_key,
                    display_name=str(config.get("display_name") or DEFAULT_APPLIANCE_CONFIGS.get(appliance_key, {}).get("display_name", appliance_key.title())),
                    kw_limit=limit,
                )
                db.session.add(entry)
            else:
                if "display_name" in config:
                    entry.display_name = str(config["display_name"]) or entry.display_name
                if "kw_limit" in config:
                    entry.kw_limit = limit
            entry.updated_at = utcnow_naive()
        db.session.commit()
        return jsonify({"configs": get_appliance_configs(), "status": "updated"})

    denied = require_device_token()
    if denied:
        return denied
    return jsonify({"configs": get_appliance_configs()})

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        denied = require_device_token()
        if denied:
            return denied
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "payload must be an object"}), 400
        names = payload.get("appliance_names")
        if not isinstance(names, list) or len(names) != 3:
            return jsonify({"error": "appliance_names must be a list of exactly 3 names"}), 400

        cleaned = []
        for item in names:
            cleaned.append((str(item).strip() or f"Appliance {len(cleaned) + 1}")[:100])

        entries = ordered_appliance_configs()
        for entry, name in zip(entries, cleaned):
            entry.display_name = name
        db.session.commit()
        return jsonify({"appliance_names": cleaned, "status": "updated"})

    denied = require_device_token()
    if denied:
        return denied
    ensure_appliance_configs()
    names = [entry.display_name for entry in ordered_appliance_configs()]
    return jsonify({"appliance_names": names})

@app.route("/api/preset", methods=["GET", "POST"])
def api_preset():
    if request.method == "POST":
        if not current_user.is_authenticated or not current_user.is_admin or not csrf_valid():
            return jsonify({"error": "authenticated session and CSRF token required"}), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "payload must be an object"}), 400
        try:
            candidate = float(payload.get("preset_limit", get_preset_limit()))
        except (TypeError, ValueError):
            return jsonify({"error": "preset_limit must be a number"}), 400
        if not math.isfinite(candidate) or candidate <= 0:
            return jsonify({"error": "preset_limit must be positive and finite"}), 400
        settings = db.session.get(SystemSettings, 1)
        if settings is None:
            settings = SystemSettings(id=1, preset_limit_watts=candidate)
            db.session.add(settings)
        else:
            settings.preset_limit_watts = candidate
        db.session.commit()
        return jsonify({"preset_limit": candidate, "status": "updated"})

    if not current_user.is_authenticated:
        return jsonify({"error": "login required"}), 401
    return jsonify({"preset_limit": get_preset_limit()})

@app.route("/api/power-events", methods=["GET"])
@login_required
def api_power_events():
    events = PowerEvent.query.order_by(PowerEvent.created_at.desc()).limit(20).all()
    payload = [{
        "id": event.id,
        "event_type": event.event_type,
        "value_watts": event.value_watts,
        "preset_limit": event.preset_limit,
        "message": event.message,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    } for event in events]
    return jsonify(payload)

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))

# ========== NEON ERROR HANDLERS ==========
@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors with neon styling"""
    return render_template("error_404.html"), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors with neon styling"""
    db.session.rollback()
    return render_template("error_500.html"), 500

@app.route("/loading")
def loading_screen():
    """Display loading screen - can be used during long operations"""
    return render_template("loading.html")

@app.route("/neon-demo")
def neon_demo():
    """Display neon effects showcase and demo page"""
    return render_template("neon_demo.html")

if __name__ == "__main__":
    with app.app_context():
        migrate_legacy_tables()
        db.create_all()
        migrate_schema()
        ensure_appliance_configs()
        get_preset_limit()
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', host="0.0.0.0", port=5000)
