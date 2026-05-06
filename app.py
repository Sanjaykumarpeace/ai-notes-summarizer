from flask import Flask, render_template, request
from google import genai
app = Flask(__name__)

# Add your API key
genai.configure(api_key="AIzaSyAjtbpYAbTKqtTajbK_SPhdYW8-EDNKnDY")

model = genai.GenerativeModel("gemini-pro")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/summarize', methods=['POST'])
def summarize():
    text = request.form['text']
    mode = request.form['mode']

    if mode == "summary":
        prompt = f"Give a short summary of this text:\n{text}"
    elif mode == "teacher":
        prompt = f"Explain this like a teacher in simple terms:\n{text}"
    elif mode == "exam":
        prompt = f"Give important exam key points from this:\n{text}"
    else:
        prompt = text

    try:
        response = model.generate_content(prompt)
        summary = response.text
    except Exception as e:
        summary = f"Error: {str(e)}"

    return render_template('index.html', summary=summary)

if __name__ == '__main__':
    app.run(debug=True)