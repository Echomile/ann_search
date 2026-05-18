import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = "single-cell-ann-system"

    # 文件上传目录
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "data", "raw")

    # 处理后数据目录
    PROCESSED_FOLDER = os.path.join(BASE_DIR, "data", "processed")

    # 索引保存目录
    INDEX_FOLDER = os.path.join(BASE_DIR, "indexes")

    # 允许上传的文件类型
    ALLOWED_EXTENSIONS = {"h5ad"}

    # 最大上传文件大小：2GB，可根据数据集大小调整
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024 * 1024