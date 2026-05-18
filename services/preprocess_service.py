import os
import json
import numpy as np
import pandas as pd
import anndata as ad
from scipy import sparse
from config import Config


def make_safe_dataset_name(file_name):
    """
    根据 h5ad 文件名生成安全的数据集目录名
    例如 liver.h5ad -> liver
    """
    base_name = os.path.basename(file_name)
    dataset_name = os.path.splitext(base_name)[0]
    dataset_name = dataset_name.replace(" ", "_")
    return dataset_name


def choose_vector_matrix(adata, vector_key="auto"):
    """
    从 AnnData 中选择用于检索的细胞向量。

    优先级：
    1. obsm['X_pca']
    2. obsm 中第一个二维向量
    3. adata.X
    """
    if vector_key != "auto":
        if vector_key in adata.obsm:
            vectors = adata.obsm[vector_key]
            source = f"obsm['{vector_key}']"
            return vectors, source

        if vector_key == "X":
            vectors = adata.X
            source = "X"
            return vectors, source

        raise ValueError(f"指定的向量来源不存在：{vector_key}")

    # 优先使用 PCA 向量
    if "X_pca" in adata.obsm:
        vectors = adata.obsm["X_pca"]
        source = "obsm['X_pca']"
        return vectors, source

    # 如果没有 X_pca，则使用 obsm 中第一个二维矩阵
    for key in adata.obsm.keys():
        value = adata.obsm[key]
        if len(value.shape) == 2:
            vectors = value
            source = f"obsm['{key}']"
            return vectors, source

    # 最后才使用原始表达矩阵 X
    vectors = adata.X
    source = "X"
    return vectors, source


def convert_vectors_to_numpy(vectors):
    """
    将向量矩阵转换为 numpy.float32 格式。
    ANN 检索库通常更适合使用 float32。
    """
    if sparse.issparse(vectors):
        vectors = vectors.toarray()

    vectors = np.asarray(vectors, dtype=np.float32)

    if len(vectors.shape) != 2:
        raise ValueError("细胞向量矩阵必须是二维矩阵")

    return vectors


def save_cell_ids(adata, save_dir):
    """
    保存细胞 ID。
    cell_ids 的顺序必须与 vectors.npy 的行顺序一致。
    """
    cell_ids = list(map(str, adata.obs_names))

    save_path = os.path.join(save_dir, "cell_ids.json")

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(cell_ids, f, ensure_ascii=False, indent=4)

    return save_path, cell_ids


def save_metadata(adata, save_dir):
    """
    保存细胞元信息。
    每一行对应一个细胞，顺序与 vectors.npy 保持一致。
    """
    metadata = adata.obs.copy()

    metadata.insert(0, "cell_id", list(map(str, adata.obs_names)))

    save_path = os.path.join(save_dir, "metadata.csv")

    metadata.to_csv(save_path, index=False, encoding="utf-8-sig")

    return save_path


def extract_and_save_vectors(h5ad_path, vector_key="auto"):
    """
    从 h5ad 文件中提取细胞向量，并保存为：
    1. vectors.npy
    2. cell_ids.json
    3. metadata.csv
    """
    if not os.path.exists(h5ad_path):
        raise FileNotFoundError(f"h5ad 文件不存在：{h5ad_path}")

    adata = ad.read_h5ad(h5ad_path)

    dataset_name = make_safe_dataset_name(h5ad_path)
    save_dir = os.path.join(Config.PROCESSED_FOLDER, dataset_name)
    os.makedirs(save_dir, exist_ok=True)

    # 选择向量矩阵
    vectors, vector_source = choose_vector_matrix(adata, vector_key)

    # 转换为 numpy.float32
    vectors = convert_vectors_to_numpy(vectors)

    # 保存 vectors.npy
    vectors_path = os.path.join(save_dir, "vectors.npy")
    np.save(vectors_path, vectors)

    # 保存 cell_ids.json
    cell_ids_path, cell_ids = save_cell_ids(adata, save_dir)

    # 保存 metadata.csv
    metadata_path = save_metadata(adata, save_dir)

    vector_info = {
        "dataset_name": dataset_name,
        "h5ad_path": h5ad_path,
        "save_dir": save_dir,
        "vectors_path": vectors_path,
        "cell_ids_path": cell_ids_path,
        "metadata_path": metadata_path,
        "vector_source": vector_source,
        "cell_count": int(vectors.shape[0]),
        "vector_dim": int(vectors.shape[1]),
        "dtype": str(vectors.dtype)
    }

    # 保存当前向量信息，后续 ANN 索引构建模块会用到
    current_vector_info_path = os.path.join(Config.PROCESSED_FOLDER, "current_vector_info.json")

    with open(current_vector_info_path, "w", encoding="utf-8") as f:
        json.dump(vector_info, f, ensure_ascii=False, indent=4)

    # 同时在数据集专属目录保存一份
    vector_info_path = os.path.join(save_dir, "vector_info.json")

    with open(vector_info_path, "w", encoding="utf-8") as f:
        json.dump(vector_info, f, ensure_ascii=False, indent=4)

    return vector_info


def load_current_vector_info():
    """
    读取当前向量文件信息。
    """
    info_path = os.path.join(Config.PROCESSED_FOLDER, "current_vector_info.json")

    if not os.path.exists(info_path):
        return None

    with open(info_path, "r", encoding="utf-8") as f:
        return json.load(f)