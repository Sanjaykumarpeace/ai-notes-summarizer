from flask import Flask, render_template, request 
app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/summarize', methods=['POST'])
def summarize():
    text = request.form['text']
    #Temporary response
    summary = "This is a sample summary of your text."
    return render_template('index.html', summary=summary)
if __name__ == '__main__':
    app.run(debug=True)