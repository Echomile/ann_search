from flask import Flask, render_template, request, redirect, url_for, flash
from config import Config
from services.data_service import save_uploaded_file, read_h5ad_info, load_current_dataset_info

app = Flask(__name__)
app.config.from_object(Config)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dataset", methods=["GET", "POST"])
def dataset():
    dataset_info = load_current_dataset_info()

    if request.method == "POST":
        try:
            file = request.files.get("file")

            saved_path = save_uploaded_file(file)
            dataset_info = read_h5ad_info(saved_path)

            flash("数据集上传并读取成功！", "success")
            return render_template("dataset.html", dataset_info=dataset_info)

        except Exception as e:
            flash(f"数据集上传或读取失败：{str(e)}", "error")
            return render_template("dataset.html", dataset_info=dataset_info)

    return render_template("dataset.html", dataset_info=dataset_info)


@app.route("/index_manage")
def index_manage():
    return render_template("index_manage.html")


@app.route("/search")
def search():
    return render_template("search.html")


@app.route("/evaluate")
def evaluate():
    return render_template("evaluate.html")


if __name__ == "__main__":
    app.run(debug=True)