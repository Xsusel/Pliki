import os
import shutil
import uuid
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, url_for, flash, abort, Response
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import db, User, File

app = Flask(__name__)
# In production, this should be a strong random secret
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_secret_key_xsus_pliki')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Move uploads outside of static for security
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 * 1024 # 100 GB Limit

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def get_disk_usage():
    # Ensure folder exists to check usage
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        try:
            os.makedirs(app.config['UPLOAD_FOLDER'])
        except OSError:
            pass # Fallback to root if creation fails
            return { 'total': 0, 'used': 0, 'free': 0, 'percent': 0 }

    total, used, free = shutil.disk_usage(app.config['UPLOAD_FOLDER'])
    return {
        'total': total,
        'used': used,
        'free': free,
        'percent': round((used / total) * 100, 1)
    }

def format_size(size):
    # Helper to display size nicely
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

@app.context_processor
def utility_processor():
    return dict(format_size=format_size)

@app.route('/')
def index():
    files = File.query.order_by(File.upload_date.desc()).all()
    return render_template('index.html', files=files)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Nieprawidłowa nazwa użytkownika lub hasło', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def dashboard():
    files = File.query.order_by(File.upload_date.desc()).all()
    usage = get_disk_usage()
    return render_template('dashboard.html', files=files, usage=usage)

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        flash('Brak pliku', 'danger')
        return redirect(url_for('dashboard'))

    file = request.files['file']
    if file.filename == '':
        flash('Nie wybrano pliku', 'danger')
        return redirect(url_for('dashboard'))

    if file:
        original_filename = secure_filename(file.filename)
        # Unique filename
        ext = os.path.splitext(original_filename)[1]
        unique_filename = f"{uuid.uuid4().hex}{ext}"

        # Ensure upload folder exists
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            os.makedirs(app.config['UPLOAD_FOLDER'])

        save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

        file.save(save_path)

        size = os.path.getsize(save_path)
        new_file = File(filename=unique_filename, original_filename=original_filename, size=size)
        db.session.add(new_file)
        db.session.commit()
        flash('Plik został przesłany pomyślnie', 'success')

    return redirect(url_for('dashboard'))

@app.route('/delete/<int:file_id>', methods=['POST'])
@login_required
def delete_file(file_id):
    file = File.query.get_or_404(file_id)
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        flash(f'Błąd podczas usuwania pliku z dysku: {e}', 'warning')

    db.session.delete(file)
    db.session.commit()
    flash('Plik usunięty', 'success')
    return redirect(url_for('dashboard'))

@app.route('/download/<int:file_id>')
def download_file(file_id):
    file = File.query.get_or_404(file_id)

    # Nginx X-Accel-Redirect
    response = Response()
    # The path here must match the alias in Nginx config
    response.headers['X-Accel-Redirect'] = f'/protected_files/{file.filename}'
    response.headers['Content-Disposition'] = f'attachment; filename="{file.original_filename}"'

    return response

def sync_files_logic():
    """Scans uploads folder and adds missing files to DB."""
    upload_folder = app.config['UPLOAD_FOLDER']
    if not os.path.exists(upload_folder):
        return 0

    count = 0
    # List files
    try:
        files_on_disk = os.listdir(upload_folder)
    except OSError:
        return 0

    for filename in files_on_disk:
        if filename.startswith('.'):
            continue

        file_path = os.path.join(upload_folder, filename)

        # Skip directories
        if not os.path.isfile(file_path):
            continue

        # Secure filename
        safe_name = secure_filename(filename)

        # Rename if needed
        if safe_name != filename:
            safe_path = os.path.join(upload_folder, safe_name)

            # Handle collision
            if os.path.exists(safe_path):
                base, ext = os.path.splitext(safe_name)
                safe_name = f"{base}_{uuid.uuid4().hex[:8]}{ext}"
                safe_path = os.path.join(upload_folder, safe_name)

            try:
                os.rename(file_path, safe_path)
                filename = safe_name
                file_path = safe_path
            except OSError as e:
                print(f"Error renaming {filename}: {e}")
                continue

        # Check DB
        if not File.query.filter_by(filename=filename).first():
            try:
                size = os.path.getsize(file_path)
                ctime = os.path.getctime(file_path)
                # Convert to naive UTC to match datetime.utcnow()
                upload_date = datetime.fromtimestamp(ctime, timezone.utc).replace(tzinfo=None)

                new_file = File(
                    filename=filename,
                    original_filename=filename, # Use safe name to avoid confusion
                    size=size,
                    upload_date=upload_date
                )
                db.session.add(new_file)
                count += 1
            except OSError as e:
                print(f"Error processing {filename}: {e}")
                continue

    if count > 0:
        db.session.commit()

    return count

@app.cli.command("sync-files")
def sync_files_command():
    """Synchronize files from upload directory to database."""
    count = sync_files_logic()
    print(f"Synchronized {count} new files.")

@app.route('/admin/sync_files', methods=['POST'])
@login_required
def sync_files():
    count = sync_files_logic()
    flash(f'Zsynchronizowano {count} nowych plików.', 'success')
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
