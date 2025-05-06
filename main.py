import os
import re
import csv
import io
import smtplib
import markdown
import html2text
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from werkzeug.utils import secure_filename
from mysql.connector import pooling, connect
import uuid
import threading
from datetime import timedelta

# Load environment variables
load_dotenv()
DEFAULT_DISPLAY_NAME = os.getenv('display_name', 'Default Sender Name')
SENDER_EMAIL = os.getenv('sender_email')
PASSWORD = os.getenv('password')
MAILER_HOST = os.getenv('MAILER_HOST', 'mail.youngmoneyent.org')
MAILER_PORT = int(os.getenv('MAILER_PORT', '465'))

# Database config and pool
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASS', ''),
    'database': os.getenv('DB_NAME', 'mail'),
}
cnxpool = pooling.MySQLConnectionPool(
    pool_name='bulk_mail_pool',
    pool_size=5,
    pool_reset_session=True,
    **DB_CONFIG
)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'a_default_key')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=1)

@app.before_request
def make_session_permanent():
    session.permanent = True

ALLOWED_EXTENSIONS_TEMPLATE = {'html', 'htm', 'md', 'txt'}
ALLOWED_EXTENSIONS_CSV = {'csv'}

def allowed_file(filename, allowed_extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def process_csv_data(csv_content):
    """
    Parse CSV into a list of dicts (rows) and a list of headers,
    but avoid sniffing newline as the delimiter on single-column data.
    """
    try:
        csv_io = io.StringIO(csv_content)
        text = csv_io.getvalue()
        # strip BOM if present
        if text.startswith('\ufeff'):
            text = text.lstrip('\ufeff')
            csv_io = io.StringIO(text)

        # Only sniff if there’s at least one comma, semicolon or tab in the sample
        sample = text[:2048]
        if any(d in sample for d in [',', ';', '\t']):
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=[',',';','\t'])
            except csv.Error:
                dialect = csv.excel
        else:
            # single-column CSV → force comma-dialect
            dialect = csv.excel

        # now read
        csv_io.seek(0)
        reader = csv.DictReader(csv_io, dialect=dialect)
        if not reader.fieldnames:
            return None, None

        # normalize header names to lowercase, stripped
        headers = [h.strip().lower() for h in reader.fieldnames if h]
        csv_io.seek(0)
        reader = csv.DictReader(csv_io, fieldnames=headers, dialect=dialect)
        rows = list(reader)
        return rows, headers

    except Exception as e:
        print(f"Error processing CSV: {e}")
        return None, None


def generate_message(template, row, headers):
    message = template
    row_lower = {str(k).lower(): str(v) for k, v in row.items() if k is not None}
    for header in headers:
        value = row_lower.get(header, "")
        esc = re.escape(header)
        message = re.sub(rf'\${{{esc}}}|\${esc}', value, message, flags=re.IGNORECASE)
    return message

def extract_subject_and_body(content):
    pattern = r"<title[^>]*>(.*?)</title>"
    match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
    if match:
        subj = match.group(1).strip()
        content = re.sub(pattern, "", content, count=1, flags=re.IGNORECASE | re.DOTALL).strip()
        content = re.sub(r"^\s*\n", "", content)
        return subj, content
    lines = content.splitlines()
    subj = "No Subject"
    body_start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not re.match(r"^\s*<", s):
            subj, body_start = s, i+1
            break
        elif s:
            body_start = i
            break
    return subj, "\n".join(lines[body_start:]).strip()

def send_email(receiver, subject, html_message, attachments, display_name):
    if not SENDER_EMAIL or not PASSWORD:
        return False, "Sender not configured."

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"{display_name} <{SENDER_EMAIL}>"
    msg['To'] = receiver

    try:
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        plain = h.handle(html_message)
    except:
        plain = html_message

    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_message, 'html', 'utf-8'))

    for file in attachments or []:
        try:
            if isinstance(file, dict):
                fname = file['filename']; data = file['data']
            else:
                fname = secure_filename(file.filename) or 'attachment'
                file.stream.seek(0); data = file.stream.read()
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(data)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{fname}"')
            msg.attach(part)
        except Exception as e:
            print(f"Attachment error: {e}")

    try:
        if MAILER_PORT == 465:
            server = smtplib.SMTP_SSL(MAILER_HOST, MAILER_PORT, timeout=30)
        else:
            server = smtplib.SMTP(MAILER_HOST, MAILER_PORT, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        with server:
            server.login(SENDER_EMAIL, PASSWORD)
            server.sendmail(SENDER_EMAIL, receiver, msg.as_string())
        return True, f"Sent to {receiver}"
    except Exception as e:
        return False, f"SMTP error: {e}"

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        src = request.form.get('template_source')
        if src == 'upload':
            tpl = request.files.get('template_file')
            if not tpl or not tpl.filename:
                flash('Select a template file.', 'error'); return redirect(request.url)
            if not allowed_file(tpl.filename, ALLOWED_EXTENSIONS_TEMPLATE):
                flash('Invalid template type.', 'error'); return redirect(request.url)
            raw = tpl.read()
            try: content = raw.decode('utf-8')
            except: content = raw.decode('latin-1')
            is_md = tpl.filename.lower().endswith('.md') or not re.search(r'</?[a-z]', content[:500])
        elif src == 'karen_inv':
            path = os.path.join(app.static_folder, 'format.html')
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            is_md = False
        else:
            content = request.form.get('email_content','')
            is_md = False

        subj_tpl = request.form.get('subject','').strip()
        display_name = request.form.get('custom_display_name','').strip() or DEFAULT_DISPLAY_NAME
        send_method = request.form.get('send_method')
        recipients, skipped, sent, failed = [], 0, 0, 0

        if send_method == 'bulk':
            csv_f = request.files.get('csv_file')
            if not csv_f or not csv_f.filename:
                flash('CSV required for bulk.', 'error'); return redirect(request.url)
            if not allowed_file(csv_f.filename, ALLOWED_EXTENSIONS_CSV):
                flash('CSV must be .csv', 'error'); return redirect(request.url)
            raw_csv = csv_f.read().decode('utf-8-sig', errors='ignore')
            rows, headers = process_csv_data(raw_csv)
            if rows is None:
                flash('CSV parse error.', 'error'); return redirect(request.url)
            col = next((h for h in headers if 'email' in h), None)
            if not col:
                flash('CSV needs an email header.', 'error'); return redirect(request.url)
            for r in rows:
                if not any(r.values()): skipped+=1; continue
                email = r.get(col,'').strip()
                if '@' not in email: skipped+=1; continue
                recipients.append((email,r))
        else:
            m = request.form.get('manual_email','').strip()
            if '@' not in m: flash('Enter valid email.', 'error'); return redirect(request.url)
            recipients = [(m,{})]; headers = []

        if not recipients:
            flash('No recipients.', 'warning'); return redirect(request.url)

        attachments_data = []
        for f in request.files.getlist('attachments'):
            if f and f.filename:
                attachments_data.append({'filename': secure_filename(f.filename), 'data': f.read()})

        task_id = uuid.uuid4().hex
        total = len(recipients); pending = total
        cnx = connect(**DB_CONFIG); cur = cnx.cursor()
        cur.execute("INSERT INTO tasks (id,total,skipped,pending) VALUES (%s,%s,%s,%s)",
                    (task_id,total,skipped,pending))
        cnx.commit(); cur.close(); cnx.close()
        session['task_id'] = task_id

        threading.Thread(
            target=send_emails_task,
            args=(task_id, recipients, content, subj_tpl, is_md, False, attachments_data, display_name, headers),
            daemon=True
        ).start()
        return redirect(url_for('index'))

    # GET: fetch active & sent-but-not-closed tasks
    cnx = connect(**DB_CONFIG); cur = cnx.cursor(dictionary=True)
    cur.execute("SELECT id, pending, total FROM tasks WHERE finished = 0 ORDER BY id")
    active_tasks = cur.fetchall()
    cur.execute("SELECT id, sent, total FROM tasks WHERE finished = 1 AND closed = 0 ORDER BY id")
    sent_tasks = cur.fetchall()
    cur.close(); cnx.close()

    # clear session task_id if it finished
    stored = session.get('task_id')
    if stored and not any(t['id'] == stored for t in active_tasks):
        session.pop('task_id', None)

    return render_template('index.html',
                           active_tasks=active_tasks,
                           sent_tasks=sent_tasks)

@app.route('/result/<task_id>')
def result(task_id):
    cnx = connect(**DB_CONFIG); cur = cnx.cursor(dictionary=True)
    cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
    task = cur.fetchone()
    cur.execute("SELECT message FROM log_entries WHERE task_id = %s ORDER BY id", (task_id,))
    logs = [r['message'] for r in cur.fetchall()]
    cur.close(); cnx.close()
    return render_template('result.html',
                           task_id=task_id,
                           task=task,
                           log_messages=logs)

@app.route('/status/<task_id>')
def status(task_id):
    cnx = connect(**DB_CONFIG); cur = cnx.cursor()
    cur.execute("SELECT sent, failed, pending, total, finished FROM tasks WHERE id = %s", (task_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); cnx.close()
        return jsonify({'error': 'unknown task'}), 404
    sent, failed, pending, total, finished = row
    cur.execute("SELECT message FROM log_entries WHERE task_id = %s ORDER BY id", (task_id,))
    logs = [r[0] for r in cur.fetchall()]
    cur.close(); cnx.close()
    return jsonify({
        'sent': sent,
        'failed': failed,
        'pending': pending,
        'total': total,
        'finished': bool(finished),
        'logs': logs
    })

@app.route('/close/<task_id>', methods=['POST'])
def close_task(task_id):
    cnx = connect(**DB_CONFIG); cur = cnx.cursor()
    cur.execute("UPDATE tasks SET closed = 1 WHERE id = %s", (task_id,))
    cnx.commit(); cur.close(); cnx.close()
    return redirect(url_for('index'))

def send_emails_task(task_id, recipients, tpl, subj_tpl, is_md, is_txt, attachments, display_name, headers):
    md = markdown.Markdown(extensions=['extra','nl2br','smarty'])
    for email, row in recipients:
        cnx = cnxpool.get_connection(); cur = cnx.cursor()
        cur.execute("INSERT INTO log_entries (task_id,message) VALUES (%s,%s)",
                    (task_id, f"SENDING: to {email}"))
        cnx.commit(); cur.close(); cnx.close()
        body = tpl
        subj = subj_tpl and generate_message(subj_tpl, row, headers) or extract_subject_and_body(tpl)[0]
        html = md.convert(body) if is_md else body
        ok, msg = send_email(email, subj, html, attachments, display_name)
        cnx = cnxpool.get_connection(); cur = cnx.cursor()
        if ok:
            cur.execute("UPDATE tasks SET sent = sent + 1 WHERE id = %s", (task_id,))
            entry = f"SUCCESS: {msg}"
        else:
            cur.execute("UPDATE tasks SET failed = failed + 1 WHERE id = %s", (task_id,))
            entry = f"FAILED: {msg}"
        cur.execute("UPDATE tasks SET pending = total - (sent + failed + skipped) WHERE id = %s", (task_id,))
        cur.execute("INSERT INTO log_entries (task_id,message) VALUES (%s,%s)", (task_id, entry))
        cnx.commit(); cur.close(); cnx.close()
    cnx = connect(**DB_CONFIG); cur = cnx.cursor()
    cur.execute("UPDATE tasks SET finished = 1 WHERE id = %s", (task_id,))
    cnx.commit(); cur.close(); cnx.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
