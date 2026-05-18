from flask import Flask, render_template, request, redirect, url_for, flash
from config import Config
from services.data_service import save_uploaded_file, read_h5ad_info, load_current_dataset_info
from services.preprocess_service import extract_and_save_vectors, load_current_vector_info

app = Flask(__name__)
app.config.from_object(Config)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dataset", methods=["GET", "POST"])
def dataset():
    dataset_info = load_current_dataset_info()
    vector_info = load_current_vector_info()

    if request.method == "POST":
        try:
            file = request.files.get("file")

            saved_path = save_uploaded_file(file)
            dataset_info = read_h5ad_info(saved_path)
            vector_info = None

            flash("数据集上传并读取成功！", "success")
            return render_template(
                "dataset.html",
                dataset_info=dataset_info,
                vector_info=vector_info
            )

        except Exception as e:
            flash(f"数据集上传或读取失败：{str(e)}", "error")
            return render_template(
                "dataset.html",
                dataset_info=dataset_info,
                vector_info=vector_info
            )

    return render_template(
        "dataset.html",
        dataset_info=dataset_info,
        vector_info=vector_info
    )


@app.route("/extract_vectors", methods=["POST"])
def extract_vectors():
    dataset_info = load_current_dataset_info()

    if dataset_info is None:
        flash("请先上传并读取 h5ad 数据集，再提取细胞向量。", "error")
        return redirect(url_for("dataset"))

    try:
        h5ad_path = dataset_info["file_path"]

        vector_info = extract_and_save_vectors(h5ad_path, vector_key="auto")

        flash("细胞向量提取并保存成功！", "success")

    except Exception as e:
        flash(f"细胞向量提取失败：{str(e)}", "error")

    return redirect(url_for("dataset"))


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