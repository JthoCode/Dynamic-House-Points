from flask import Flask, render_template, redirect, request, url_for, jsonify
from cs50 import SQL
import os

from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
# stable secret key from env in production
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

# Flask-Login setup
login_manager = LoginManager()
login_manager.login_view = 'password'
login_manager.init_app(app)


class User(UserMixin):
    def __init__(self, id):
        self.id = id


@login_manager.user_loader
def load_user(user_id):
    # we only have one admin user with id 'admin'
    if user_id == 'admin':
        return User('admin')
    return None

# Use absolute path for database
db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'points.db')
db = SQL(f"sqlite:///{db_path}")

HOUSES = ["devotus", "respectus", "amare", "nobilis", "animo"]

def init_db():
    """Initialize database tables if they don't exist."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS event_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            points INTEGER NOT NULL,
            event TEXT NOT NULL,
            placing INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS current_points (
            name TEXT PRIMARY KEY,
            points INTEGER NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Initialize all houses with 0 points if they don't exist
    for house in HOUSES:
        existing = db.execute("SELECT points FROM current_points WHERE name = ?", house)
        if not existing:
            db.execute("INSERT INTO current_points (name, points) VALUES (?, ?)", house, 0)
    # ensure locked setting exists
    locked = db.execute("SELECT value FROM settings WHERE key = ?", "locked")
    if not locked:
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", "locked", "0")

init_db()

def get_all_house_points():
    """Retrieve current points for all houses."""
    points_dict = {}
    rows = db.execute("SELECT name, points FROM current_points ORDER BY name")
    for row in rows:
        points_dict[row['name']] = row['points']
    return points_dict


def get_setting(key):
    rows = db.execute("SELECT value FROM settings WHERE key = ?", key)
    if not rows:
        return None
    return rows[0]['value']


def set_setting(key, value):
    db.execute("UPDATE settings SET value = ? WHERE key = ?", str(value), key)

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if request.method == 'GET':
        house_points = get_all_house_points()
        return render_template(
            "admin.html",
            devotus=house_points.get('devotus', 0),
            respectus=house_points.get('respectus', 0),
            amare=house_points.get('amare', 0),
            nobilis=house_points.get('nobilis', 0),
            animo=house_points.get('animo', 0)
        )
    if request.method == 'POST':
        # Collect points for each house
        house_data = {}
        for house in HOUSES:
            try:
                points_str = request.form.get(house, "").strip()
                points = int(points_str) if points_str else 0
                event = request.form.get(f"{house}_event", "").strip()
                placing_str = request.form.get(f"{house}_placing", "").strip()
                placing = int(placing_str) if placing_str else 0
                house_data[house] = {'points': points, 'event': event, 'placing': placing}
            except ValueError:
                return render_template("admin.html", error=f"Invalid input for {house}")
        
        # Update database: record in history and update current points
        for house, data in house_data.items():
            db.execute(
                "INSERT INTO event_history (name, points, event, placing) VALUES (?, ?, ?, ?)",
                house, data['points'], data['event'], data['placing']
            )
            db.execute(
                "UPDATE current_points SET points = points + ? WHERE name = ?",
                data['points'], house
            )
        
        return redirect('/')

@app.route('/')
def index():
    house_points = get_all_house_points()
    locked = get_setting('locked') == '1'
    return render_template(
        "index.html",
        devotus=house_points.get('devotus', 0),
        respectus=house_points.get('respectus', 0),
        amare=house_points.get('amare', 0),
        nobilis=house_points.get('nobilis', 0),
        animo=house_points.get('animo', 0),
        locked=locked,
        is_admin=current_user.is_authenticated
    )

@app.route('/history/<house>')
def history(house):
    """Return event history for a specific house as JSON."""
    if house not in HOUSES:
        return jsonify({'error': 'Invalid house'}), 400
    
    events = db.execute(
        "SELECT points, event, placing, timestamp FROM event_history WHERE name = ? ORDER BY timestamp DESC",
        house
    )
    return jsonify({'house': house, 'events': events})


@app.route('/admin/reset', methods=['POST'])
@login_required
def admin_reset():
    # Clear history and reset current points and unlock
    db.execute("DELETE FROM event_history")
    db.execute("UPDATE current_points SET points = 0")
    set_setting('locked', '0')
    return jsonify({'success': True})

@app.route('/admin/lock', methods=['POST'])
@login_required
def admin_lock():
    # Lock scores so homepage no longer shows them
    set_setting('locked', '1')
    return jsonify({'success': True})

@app.route('/admin/undo', methods=['POST'])
@login_required
def admin_undo():
    # Get the most recent entry
    latest = db.execute(
        "SELECT id, name, points FROM event_history ORDER BY id DESC LIMIT 1"
    )
    
    if not latest:
        return jsonify({'success': False, 'error': 'No entries to undo'})
    
    entry = latest[0]
    entry_id = entry['id']
    house_name = entry['name']
    points = entry['points']
    
    # Delete the entry
    db.execute("DELETE FROM event_history WHERE id = ?", entry_id)
    
    # Subtract the points from current_points
    db.execute(
        "UPDATE current_points SET points = points - ? WHERE name = ?",
        points, house_name
    )
    
    return jsonify({'success': True})

@app.route('/reset-password', methods=['GET', 'POST'])
@login_required
def reset_password():

    if request.method == 'GET':
        return render_template('reset.html')

    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')

    row = db.execute(
        "SELECT value FROM settings WHERE key = ?",
        "password_hash"
    )

    pw_hash = row[0]["value"]

    if not check_password_hash(pw_hash, current_password):
        return render_template(
            'reset.html',
            error='Current password is incorrect'
        )

    db.execute(
        "UPDATE settings SET value = ? WHERE key = ?",
        generate_password_hash(new_password),
        "password_hash"
    )

    return redirect('/admin')

@app.route('/password', methods=['GET', 'POST'])
def password():

    if request.method == 'GET':
        return render_template("password.html")

    password = request.form.get("password")

    if not password:
        return render_template(
            "password.html",
            error="Password is required"
        )


    password_hash = db.execute(
        "SELECT value FROM settings WHERE key = ?",
        "password_hash"
    )

    pw_hash = password_hash[0]["value"]

    if check_password_hash(pw_hash, password):
        user = User('admin')
        login_user(user)
        return redirect(url_for('admin'))

    return render_template(
        "password.html",
        error="Incorrect password"
    )

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))