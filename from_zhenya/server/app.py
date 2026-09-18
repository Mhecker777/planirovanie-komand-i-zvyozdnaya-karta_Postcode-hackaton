from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import pandas as pd
from process import process_data, from_xlsx_to_csv

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

app = Flask(__name__, static_folder=None)

CSV_PATH = BASE_DIR / "db" / "data.csv"
IMG_DIR = ROOT_DIR / "assets" / "img"


@app.route("/")
def root():
    return send_from_directory(ROOT_DIR, "index.html")


@app.route("/index")
def index():
    return send_from_directory(ROOT_DIR, "index.html")


@app.route("/login")
def login():
    return send_from_directory(ROOT_DIR, "login.html")


@app.route("/reports")
def reports():
    return send_from_directory(ROOT_DIR, "reports.html")


@app.route("/css/<path:filename>")
def css(filename):
    return send_from_directory(ROOT_DIR / "css", filename)


@app.route("/js/<path:filename>")
def js(filename):
    return send_from_directory(ROOT_DIR / "js", filename)


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(ROOT_DIR / "assets", filename)


def save_and_process(new_df):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_df.to_csv(CSV_PATH, index=False)
    process_data()
    return len(new_df)


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Файл не выбран"}), 400

    try:
        new_df = pd.read_csv(file)
    except Exception as e:
        return jsonify({"error": f"Ошибка чтения CSV: {e}"}), 400

    added = save_and_process(new_df)
    return jsonify({"ok": True, "added": added})


@app.route("/upload-xlsx", methods=["POST"])
def upload_xlsx():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Файл не выбран"}), 400

    try:
        new_df = from_xlsx_to_csv(file)
    except Exception as e:
        return jsonify({"error": f"Ошибка чтения XLSX: {e}"}), 400

    added = save_and_process(new_df)
    return jsonify({"ok": True, "added": added})


@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": f"Внутренняя ошибка: {e}"}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)