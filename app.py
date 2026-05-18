from flask import Flask, render_template, request, redirect, url_for, flash
from config import Config
from services.data_service import save_uploaded_file, read_h5ad_info, load_current_dataset_info
from services.preprocess_service import extract_and_save_vectors, load_current_vector_info
from services.ann_service import build_hnsw_index, load_current_index_info

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


@app.route("/index_manage", methods=["GET", "POST"])
def index_manage():
    vector_info = load_current_vector_info()
    index_info = load_current_index_info()

    if request.method == "POST":
        try:
            if vector_info is None:
                flash("请先在数据集管理页面提取细胞向量，再构建索引。", "error")
                return redirect(url_for("index_manage"))

            metric = request.form.get("metric", "l2")
            M = int(request.form.get("M", 16))
            ef_construction = int(request.form.get("ef_construction", 200))
            ef_search = int(request.form.get("ef_search", 50))

            index_info = build_hnsw_index(
                metric=metric,
                M=M,
                ef_construction=ef_construction,
                ef_search=ef_search
            )

            flash("HNSW ANN 索引构建成功！", "success")

        except Exception as e:
            flash(f"HNSW 索引构建失败：{str(e)}", "error")

        return redirect(url_for("index_manage"))

    return render_template(
        "index_manage.html",
        vector_info=vector_info,
        index_info=index_info
    )


@app.route("/search")
def search():
    return render_template("search.html")


@app.route("/evaluate")
def evaluate():
    return render_template("evaluate.html")


if __name__ == "__main__":
    app.run(debug=True)