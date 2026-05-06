from flask import Flask, render_template, request
from google import genai

app = Flask(__name__)

# Gemini Client
client = genai.Client(api_key="AIzaSyBBD_vqcf_9EPfo46w3s92vKldAVHcz3qQ")

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

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )

        summary = response.text

    except Exception as e:
        summary = f"Error: {str(e)}"

    return render_template('index.html', summary=summary)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)