# Smart Notes Studio

A Flask study assistant that summarizes notes, generates flashcards, quizzes, and MCQs, supports PDF/DOCX/YouTube inputs, saves signed-in user history, exports summaries to PDF, and lets you chat with selected notes.

## Setup

1. Create and activate a virtual environment.
2. Install dependencies: `pip install -r requirements.txt`
3. Set your Gemini API key: `set GEMINI_API_KEY=your_key_here` on Windows CMD or `$env:GEMINI_API_KEY="your_key_here"` in PowerShell.
4. Run the app: `python app.py`
5. Open `http://localhost:5000`.

Open the Flask URL above, not `templates/index.html` directly. The direct file preview is only a fallback preview; generation, login, export, and history need the Flask server.

## Features

- Text, PDF, DOCX, and YouTube transcript summarization
- Tone options: Beginner, Technical, Story mode, Bullet points
- Smart Study Mode: summaries, tutor explanations, exam points, flashcards, quizzes, MCQs
- Basic local accounts with saved summary history
- Chat with saved notes
- Copy and PDF export
- Responsive dark mode UI
- Simple in-memory AI request rate limiting

## Notes

The SQLite database is created automatically as `smart_notes.db` when the app starts. Do not commit real API keys or local database files.
