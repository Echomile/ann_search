import os
import json
import time

import numpy as np
import pandas as pd

from services.preprocess_service import load_current_vector_info
from services.ann_service import load_hnsw_index


def load_cell_ids(cell_ids_path):
    """
    读取 cell_ids.json。
    cell_ids 的顺序必须与 vectors.npy 的行顺序一致。
    """
    if not os.path.exists(cell_ids_path):
        raise FileNotFoundError(f"cell_ids.json 不存在：{cell_ids_path}")

    with open(cell_ids_path, "r", encoding="utf-8") as f:
        cell_ids = json.load(f)

    return list(map(str, cell_ids))


def load_metadata(metadata_path):
    """
    读取 metadata.csv。
    每一行对应一个细胞，顺序与 vectors.npy 保持一致。
    """
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(f"metadata.csv 不存在：{metadata_path}")

    metadata = pd.read_csv(metadata_path)

    # 防止页面显示 NaN
    metadata = metadata.fillna("")

    return metadata


def choose_display_columns(metadata):
    """
    选择页面展示的元信息字段。
    如果字段太多，全部展示会导致页面太宽，所以优先展示常见字段。
    """
    preferred_columns = [
        "cell_id",
        "cell_type",
        "celltype",
        "cell_type_original",
        "tissue",
        "organ",
        "donor_id",
        "sample_id",
        "age",
        "sex",
        "disease",
        "batch"
    ]

    display_columns = []

    for col in preferred_columns:
        if col in metadata.columns and col not in display_columns:
            display_columns.append(col)

    # 如果没有匹配到常见字段，则展示前 6 个字段
    if len(display_columns) == 0:
        display_columns = list(metadata.columns[:6])

    # 保证 cell_id 尽量在第一列
    if "cell_id" in metadata.columns and "cell_id" not in display_columns:
        display_columns.insert(0, "cell_id")

    # 最多展示 8 个字段，避免页面过宽
    return display_columns[:8]


def search_similar_cells_by_id(query_cell_id, top_k=10):
    """
    根据输入细胞编号，基于 HNSW 索引执行 Top-K 相似细胞检索。
    """

    vector_info = load_current_vector_info()

    if vector_info is None:
        raise ValueError("未找到向量文件信息，请先提取 vectors.npy")

    vectors_path = vector_info["vectors_path"]
    cell_ids_path = vector_info["cell_ids_path"]
    metadata_path = vector_info["metadata_path"]

    if not os.path.exists(vectors_path):
        raise FileNotFoundError(f"vectors.npy 不存在：{vectors_path}")

    vectors = np.load(vectors_path).astype(np.float32)
    cell_ids = load_cell_ids(cell_ids_path)
    metadata = load_metadata(metadata_path)

    if vectors.shape[0] != len(cell_ids):
        raise ValueError("vectors.npy 行数与 cell_ids.json 数量不一致")

    if vectors.shape[0] != len(metadata):
        raise ValueError("vectors.npy 行数与 metadata.csv 行数不一致")

    query_cell_id = str(query_cell_id).strip()

    if query_cell_id == "":
        raise ValueError("查询细胞编号不能为空")

    cell_id_to_index = {cell_id: idx for idx, cell_id in enumerate(cell_ids)}

    if query_cell_id not in cell_id_to_index:
        raise ValueError(f"未找到该细胞编号：{query_cell_id}")

    query_index = cell_id_to_index[query_cell_id]
    query_vector = vectors[query_index].reshape(1, -1)

    index, index_info = load_hnsw_index()

    top_k = int(top_k)

    if top_k <= 0:
        raise ValueError("Top-K 必须大于 0")

    # 因为查询细胞本身通常会以距离 0 返回，所以多查 1 个，然后排除自身
    search_k = min(top_k + 1, len(cell_ids))

    start_time = time.time()

    labels, distances = index.knn_query(query_vector, k=search_k)

    query_time = time.time() - start_time

    display_columns = choose_display_columns(metadata)

    results = []

    for label, distance in zip(labels[0], distances[0]):
        label = int(label)

        # 排除查询细胞本身
        if label == query_index:
            continue

        result_cell_id = cell_ids[label]
        row = metadata.iloc[label].astype(str).to_dict()

        display_metadata = {}

        for col in display_columns:
            display_metadata[col] = row.get(col, "")

        results.append({
            "rank": len(results) + 1,
            "cell_id": result_cell_id,
            "distance": round(float(distance), 6),
            "metadata": display_metadata
        })

        if len(results) >= top_k:
            break

    search_result = {
        "query_cell_id": query_cell_id,
        "query_index": int(query_index),
        "top_k": top_k,
        "actual_count": len(results),
        "query_time_ms": round(query_time * 1000, 4),
        "metric": index_info["metric"],
        "index_type": index_info["index_type"],
        "dataset_name": vector_info["dataset_name"],
        "vector_source": vector_info["vector_source"],
        "display_columns": display_columns,
        "results": results
    }

    return search_result