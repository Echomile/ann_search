import os
import json
import anndata as ad
import pandas as pd
from werkzeug.utils import secure_filename
from config import Config


def allowed_file(filename):
    """
    判断上传文件是否为 h5ad 文件
    """
    return "." in filename and filename.rsplit(".", 1)[1].lower() in Config.ALLOWED_EXTENSIONS


def save_uploaded_file(file):
    """
    保存上传的 h5ad 文件
    """
    if file is None or file.filename == "":
        raise ValueError("未选择上传文件")

    if not allowed_file(file.filename):
        raise ValueError("文件格式错误，请上传 .h5ad 文件")

    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

    filename = secure_filename(file.filename)
    save_path = os.path.join(Config.UPLOAD_FOLDER, filename)

    file.save(save_path)

    return save_path


def read_h5ad_info(file_path):
    """
    读取 h5ad 文件，并返回 AnnData 的基本信息
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError("数据文件不存在")

    adata = ad.read_h5ad(file_path)

    # 基本信息
    cell_count = adata.n_obs
    gene_count = adata.n_vars
    x_shape = adata.X.shape

    # obs / var / obsm / layers 信息
    obs_columns = list(adata.obs.columns)
    var_columns = list(adata.var.columns)
    obsm_keys = list(adata.obsm.keys())
    layer_keys = list(adata.layers.keys())

    # 判断推荐向量来源
    if "X_pca" in adata.obsm:
        vector_source = "obsm['X_pca']"
        vector_dim = adata.obsm["X_pca"].shape[1]
    elif len(obsm_keys) > 0:
        first_key = obsm_keys[0]
        vector_source = f"obsm['{first_key}']"
        vector_dim = adata.obsm[first_key].shape[1]
    else:
        vector_source = "X"
        vector_dim = adata.X.shape[1]

    # 提取前 5 条细胞元信息，用于页面展示
    if len(adata.obs) > 0:
        obs_preview = adata.obs.head(5).reset_index().rename(columns={"index": "cell_id"})
        obs_preview = obs_preview.astype(str).to_dict(orient="records")
    else:
        obs_preview = []

    dataset_info = {
        "file_name": os.path.basename(file_path),
        "file_path": file_path,
        "cell_count": int(cell_count),
        "gene_count": int(gene_count),
        "x_shape": str(x_shape),
        "obs_columns": obs_columns,
        "var_columns": var_columns,
        "obsm_keys": obsm_keys,
        "layer_keys": layer_keys,
        "vector_source": vector_source,
        "vector_dim": int(vector_dim),
        "obs_preview": obs_preview
    }

    save_dataset_info(dataset_info)

    return dataset_info


def save_dataset_info(dataset_info):
    """
    保存当前数据集信息，方便后续索引构建模块使用
    """
    os.makedirs(Config.PROCESSED_FOLDER, exist_ok=True)

    info_path = os.path.join(Config.PROCESSED_FOLDER, "current_dataset_info.json")

    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(dataset_info, f, ensure_ascii=False, indent=4)


def load_current_dataset_info():
    """
    读取当前数据集信息
    """
    info_path = os.path.join(Config.PROCESSED_FOLDER, "current_dataset_info.json")

    if not os.path.exists(info_path):
        return None

    with open(info_path, "r", encoding="utf-8") as f:
        return json.load(f)