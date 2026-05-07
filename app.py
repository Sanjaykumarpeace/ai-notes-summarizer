import io
import json
import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime
from functools import wraps
from html import escape
from urllib.parse import parse_qs, urlparse

from docx import Document
from flask import (
    Flask,
    flash,
    get_flashed_messages,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from google import genai
from PyPDF2 import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from youtube_transcript_api import YouTubeTranscriptApi

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "smart_notes.db")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX_REQUESTS = 8

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me-for-production")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

client = None
if os.getenv("GEMINI_API_KEY"):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

rate_buckets = defaultdict(deque)

TONE_PROMPTS = {
    "beginner": "Use beginner-friendly language, define jargon, and keep the explanation welcoming.",
    "technical": "Use precise technical language, preserve important terminology, and explain mechanisms.",
    "story": "Explain as a memorable story with simple analogies while staying accurate.",
    "bullets": "Use concise bullet points with clear hierarchy and no filler.",
}

TASK_PROMPTS = {
    "summary": "Create a polished study summary with the most important ideas.",
    "teacher": "Teach these notes step by step like a patient tutor.",
    "exam": "Extract exam-focused key points, formulas, definitions, and likely question areas.",
    "flashcards": "Create 10-15 flashcards. Format each as Q: question and A: answer.",
    "quiz": "Create 8 short-answer quiz questions with answers.",
    "mcq": "Create 8 multiple-choice questions with four options and mark the correct answer.",
}


def json_dumps(payload):
    return json.dumps(payload, ensure_ascii=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                tone TEXT NOT NULL,
                task TEXT NOT NULL,
                source_text TEXT NOT NULL,
                output TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )


init_db()


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def rate_limited(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = f"{request.remote_addr}:{session.get('user_id', 'guest')}"
        now = time.time()
        bucket = rate_buckets[key]
        while bucket and now - bucket[0] > RATE_LIMIT_WINDOW:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            flash("Too many AI requests. Please wait a minute and try again.", "error")
            return redirect(url_for("home"))
        bucket.append(now)
        return fn(*args, **kwargs)

    return wrapper


def call_ai(prompt):
    if not client:
        raise RuntimeError("Set GEMINI_API_KEY in your environment to enable AI generation.")
    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    return response.text or "No response was returned."


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def extract_pdf(file_storage):
    reader = PdfReader(file_storage.stream)
    pages = [page.extract_text() or "" for page in reader.pages]
    return clean_text("\n".join(pages))


def extract_docx(file_storage):
    document = Document(file_storage.stream)
    return clean_text("\n".join(paragraph.text for paragraph in document.paragraphs))


def extract_youtube_id(url):
    parsed = urlparse(url)
    if parsed.hostname in {"youtu.be"}:
        return parsed.path.lstrip("/")
    if parsed.hostname and "youtube.com" in parsed.hostname:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [""])[0]
        if parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[2]
    return ""


def extract_youtube_transcript(url):
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError("Enter a valid YouTube video URL.")

    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
    else:
        fetched = YouTubeTranscriptApi().fetch(video_id)
        transcript = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else fetched
    return clean_text(" ".join(item["text"] for item in transcript))


def source_from_request():
    source_type = request.form.get("source_type", "text")
    title = clean_text(request.form.get("title")) or "Untitled notes"

    if source_type == "text":
        text = clean_text(request.form.get("text"))
    elif source_type == "youtube":
        text = extract_youtube_transcript(request.form.get("youtube_url", ""))
    elif source_type == "file":
        uploaded = request.files.get("notes_file")
        if not uploaded or not uploaded.filename:
            raise ValueError("Upload a PDF or DOCX file.")
        filename = secure_filename(uploaded.filename)
        extension = os.path.splitext(filename)[1].lower()
        title = title if title != "Untitled notes" else os.path.splitext(filename)[0]
        if extension == ".pdf":
            text = extract_pdf(uploaded)
        elif extension == ".docx":
            text = extract_docx(uploaded)
        else:
            raise ValueError("Only PDF and DOCX uploads are supported.")
    else:
        raise ValueError("Choose a valid input type.")

    if not text:
        raise ValueError("I could not find readable text in that source.")
    if len(text) > 60000:
        text = text[:60000]
    return title, source_type, text


def build_prompt(text, task, tone):
    task_instruction = TASK_PROMPTS.get(task, TASK_PROMPTS["summary"])
    tone_instruction = TONE_PROMPTS.get(tone, TONE_PROMPTS["beginner"])
    return f"""
You are Smart Notes, an expert study assistant.

Task: {task_instruction}
Tone: {tone_instruction}

Make the output useful for revision. Use clear headings where helpful.

Notes:
{text}
""".strip()


def save_summary(title, source_type, tone, task, source_text, output):
    user = current_user()
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO summaries (user_id, title, source_type, tone, task, source_text, output, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"] if user else None,
                title,
                source_type,
                tone,
                task,
                source_text,
                output,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        return cursor.lastrowid


def recent_summaries():
    user = current_user()
    if not user:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT id, title, source_type, tone, task, output, created_at
            FROM summaries
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 8
            """,
            (user["id"],),
        ).fetchall()


def row_to_dict(row):
    return dict(row) if row else None


def build_state(selected_summary=None, chat=None):
    user = current_user()
    messages = [
        {"category": category, "message": message}
        for category, message in get_flashed_messages(with_categories=True)
    ]
    return {
        "user": row_to_dict(user),
        "history": [row_to_dict(item) for item in recent_summaries()],
        "selected_summary": row_to_dict(selected_summary),
        "chat": chat,
        "messages": messages,
        "tone_labels": {
            "beginner": "Beginner",
            "technical": "Technical",
            "story": "Story mode",
            "bullets": "Bullet points",
        },
        "task_labels": {
            "summary": "Summary",
            "teacher": "Tutor",
            "exam": "Exam points",
            "flashcards": "Flashcards",
            "quiz": "Quiz",
            "mcq": "MCQs",
        },
    }


@app.context_processor
def inject_globals():
    return {
        "user": current_user(),
        "history": recent_summaries(),
        "tone_labels": {
            "beginner": "Beginner",
            "technical": "Technical",
            "story": "Story mode",
            "bullets": "Bullet points",
        },
        "task_labels": {
            "summary": "Summary",
            "teacher": "Tutor",
            "exam": "Exam points",
            "flashcards": "Flashcards",
            "quiz": "Quiz",
            "mcq": "MCQs",
        },
    }


@app.route("/")
def home():
    summary_id = request.args.get("summary_id", type=int)
    selected_summary = None
    if summary_id:
        with get_db() as conn:
            selected_summary = conn.execute(
                "SELECT * FROM summaries WHERE id = ?", (summary_id,)
            ).fetchone()
    return render_template("index.html", state_json=json_dumps(build_state(selected_summary)))


@app.route("/summarize", methods=["POST"])
@rate_limited
def summarize():
    task = request.form.get("task", "summary")
    tone = request.form.get("tone", "beginner")
    try:
        title, source_type, source_text = source_from_request()
        output = call_ai(build_prompt(source_text, task, tone))
        summary_id = save_summary(title, source_type, tone, task, source_text, output)
        flash("Your study material is ready.", "success")
        return redirect(url_for("home", summary_id=summary_id))
    except Exception as exc:
        flash(str(exc), "error")
        return redirect(url_for("home"))


@app.route("/chat", methods=["POST"])
@rate_limited
def chat():
    summary_id = request.form.get("summary_id", type=int)
    question = clean_text(request.form.get("question"))
    if not summary_id or not question:
        flash("Open a saved summary and ask a question.", "error")
        return redirect(url_for("home", summary_id=summary_id or ""))

    with get_db() as conn:
        note = conn.execute("SELECT * FROM summaries WHERE id = ?", (summary_id,)).fetchone()
    if not note:
        flash("That summary could not be found.", "error")
        return redirect(url_for("home"))

    prompt = f"""
Answer the question using only these notes. If the answer is not present, say what is missing.

Question: {question}

Notes:
{note['source_text']}
""".strip()
    try:
        answer = call_ai(prompt)
        chat_payload = {"question": question, "answer": answer}
        return render_template("index.html", state_json=json_dumps(build_state(note, chat_payload)))
    except Exception as exc:
        flash(str(exc), "error")
        return redirect(url_for("home", summary_id=summary_id))


@app.route("/register", methods=["POST"])
def register():
    username = clean_text(request.form.get("username")).lower()
    password = request.form.get("password", "")
    if len(username) < 3 or len(password) < 6:
        flash("Use a username of 3+ characters and a password of 6+ characters.", "error")
        return redirect(url_for("home"))
    try:
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), datetime.utcnow().isoformat()),
            )
            session["user_id"] = cursor.lastrowid
        flash("Account created. Your future summaries will be saved.", "success")
    except sqlite3.IntegrityError:
        flash("That username is already taken.", "error")
    return redirect(url_for("home"))


@app.route("/login", methods=["POST"])
def login():
    username = clean_text(request.form.get("username")).lower()
    password = request.form.get("password", "")
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user and check_password_hash(user["password_hash"], password):
        session["user_id"] = user["id"]
        flash("Welcome back.", "success")
    else:
        flash("Invalid username or password.", "error")
    return redirect(url_for("home"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("home"))


@app.route("/export/<int:summary_id>.pdf")
def export_pdf(summary_id):
    with get_db() as conn:
        note = conn.execute("SELECT * FROM summaries WHERE id = ?", (summary_id,)).fetchone()
    if not note:
        flash("Summary not found.", "error")
        return redirect(url_for("home"))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title=note["title"])
    styles = getSampleStyleSheet()
    story = [
        Paragraph(note["title"], styles["Title"]),
        Spacer(1, 12),
        Paragraph(f"Mode: {note['task']} | Tone: {note['tone']}", styles["Normal"]),
        Spacer(1, 12),
    ]
    for block in note["output"].splitlines():
        if block.strip():
            story.append(Paragraph(escape(block.strip()), styles["BodyText"]))
            story.append(Spacer(1, 8))
    doc.build(story)
    buffer.seek(0)

    response = make_response(buffer.read())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename={secure_filename(note['title']) or 'summary'}.pdf"
    return response


@app.route("/health")
def health():
    return {"ok": True, "ai_configured": bool(client)}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
