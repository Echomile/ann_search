import os
import json
import time
from datetime import datetime

import hnswlib
import numpy as np

from config import Config
from services.preprocess_service import load_current_vector_info


def build_hnsw_index(metric="l2", M=16, ef_construction=200, ef_search=50):
    """
    基于 vectors.npy 构建 HNSW ANN 索引。

    参数说明：
    metric:
        l2      欧氏距离
        cosine  余弦距离
        ip      内积距离

    M:
        HNSW 图中每个节点的最大连接数，值越大召回率可能越高，但索引更大。

    ef_construction:
        构建索引时的搜索范围，值越大构建越慢，但索引质量可能更高。

    ef_search:
        查询时的搜索范围，值越大查询越慢，但召回率可能更高。
    """

    vector_info = load_current_vector_info()

    if vector_info is None:
        raise ValueError("未找到向量文件信息，请先提取 vectors.npy")

    vectors_path = vector_info["vectors_path"]

    if not os.path.exists(vectors_path):
        raise FileNotFoundError(f"vectors.npy 不存在：{vectors_path}")

    vectors = np.load(vectors_path)

    if len(vectors.shape) != 2:
        raise ValueError("vectors.npy 必须是二维矩阵")

    vectors = vectors.astype(np.float32)

    cell_count, vector_dim = vectors.shape

    ids = np.arange(cell_count)

    dataset_name = vector_info["dataset_name"]

    index_dir = os.path.join(Config.INDEX_FOLDER, dataset_name)
    os.makedirs(index_dir, exist_ok=True)

    index_file_name = f"hnsw_{metric}_M{M}_efC{ef_construction}.bin"
    index_path = os.path.join(index_dir, index_file_name)

    start_time = time.time()

    index = hnswlib.Index(space=metric, dim=vector_dim)

    index.init_index(
        max_elements=cell_count,
        ef_construction=ef_construction,
        M=M
    )

    index.add_items(vectors, ids)

    index.set_ef(ef_search)

    index.save_index(index_path)

    build_time = time.time() - start_time

    index_info = {
        "dataset_name": dataset_name,
        "index_type": "HNSW",
        "metric": metric,
        "M": int(M),
        "ef_construction": int(ef_construction),
        "ef_search": int(ef_search),
        "cell_count": int(cell_count),
        "vector_dim": int(vector_dim),
        "index_path": index_path,
        "vectors_path": vectors_path,
        "build_time_seconds": round(build_time, 4),
        "created_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "已构建"
    }

    current_index_info_path = os.path.join(Config.INDEX_FOLDER, "current_index_info.json")

    with open(current_index_info_path, "w", encoding="utf-8") as f:
        json.dump(index_info, f, ensure_ascii=False, indent=4)

    dataset_index_info_path = os.path.join(index_dir, "index_info.json")

    with open(dataset_index_info_path, "w", encoding="utf-8") as f:
        json.dump(index_info, f, ensure_ascii=False, indent=4)

    return index_info


def load_current_index_info():
    """
    读取当前 HNSW 索引信息。
    """

    info_path = os.path.join(Config.INDEX_FOLDER, "current_index_info.json")

    if not os.path.exists(info_path):
        return None

    with open(info_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_hnsw_index():
    """
    加载当前 HNSW 索引，后续 search_service.py 检索时会用到。
    """

    index_info = load_current_index_info()

    if index_info is None:
        raise ValueError("未找到索引信息，请先构建 HNSW 索引")

    index_path = index_info["index_path"]

    if not os.path.exists(index_path):
        raise FileNotFoundError(f"HNSW 索引文件不存在：{index_path}")

    metric = index_info["metric"]
    vector_dim = index_info["vector_dim"]
    ef_search = index_info.get("ef_search", 50)

    index = hnswlib.Index(space=metric, dim=vector_dim)
    index.load_index(index_path)
    index.set_ef(ef_search)

    return index, index_info