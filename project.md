软件工程大作业 (Software Engineering Final Project) - 课程大作业提交

## 项目目标 (Project Goal)

开发一个面向单细胞高维向量数据 (Single-cell high-dimensional vector data) 的近似最近邻 (Approximate Nearest Neighbor, ANN) 检索系统，为大规模单细胞数据提供高效的相似样本检索能力 。系统需支持单细胞数据的读取与预处理 (Preprocessing)、精确检索 (Exact search) 与近似检索 (Approximate search)、多种索引结构 (Index structures) 管理、查询结果展示以及性能评测等功能 。用户可以通过 Web 页面输入查询细胞或查询向量，设置检索参数，获取 top-k 相似结果，并查看查询耗时、索引状态和评测指标等信息 。

## 背景 (Background)

- 随着单细胞测序技术 (Single-cell sequencing technology) 的发展，单细胞数据的规模不断扩大，一个实验往往可以产生数十万个细胞样本 。

- 经过数值化表示后，每个细胞都可以看作一个高维向量 (High-dimensional vector) 。

- 把组织中的单个细胞分离出来，分别测量每个细胞内部的分子信息，再通过测序读出这些信息 。

- 测序完成后，可以统计每个细胞中各个基因的表达量，从而得到一个“细胞 × 基因”的表达矩阵 (Expression matrix) 。

- 传统的精确最近邻搜索方法在数据规模较大、维度较高的情况下，往往存在查询效率低、响应时间长、计算资源消耗大的问题 。

- 近似最近邻检索 (Approximate Nearest Neighbor, ANN) 技术能够在保证较高检索质量的同时显著降低查询开销，已广泛应用于图像搜索、推荐系统等场景，也适合用于单细胞数据 。

功能模块 (Function Modules)

- **用户信息模块 (User Information Module)**：用户可以通过账号密码注册登录该系统并查看可视化系统，管理员可以进行用户管理等 。

- **数据管理模块 (Data Management Module)**：负责单细胞数据的导入、读取和组织管理 。系统将单细胞样本表示为高维向量，并对输入数据进行格式校验和基础预处理 。

- **索引构建模块 (Index Construction Module)**：负责根据输入数据建立检索索引，并完成索引的构建、保存和加载操作 。

- **查询检索模块 (Query Retrieval Module)**：系统的核心模块，负责接收用户输入的查询细胞编号或查询向量，按照设定的参数执行相似性检索，并返回 top-k 个最相近的结果 。需支持基本的检索参数设置，如索引类型、距离度量方式和返回结果数量等 。

- **可视化展示模块 (Visualization Display Module)**：负责将检索结果以直观的形式展示给用户 。

## 数据结构与处理 (Data Structure and Processing)

AnnData (.h5ad)

在 Python 单细胞分析生态中，最常用的数据结构为 AnnData，其文件后缀为 `.h5ad` 。AnnData 主要包含以下字段 ：

- **X**: 基因表达矩阵 (Gene expression matrix)，是 AnnData 最核心的数据部分 。行代表细胞 (Cell)，列代表基因 (Gene)，矩阵中的值表示基因表达量 。

- **obs**: 细胞元信息 (Cell meta-information) 。

- **var**: 基因元信息 (Gene meta-information) 。

- **obsm**: 降维后的特征 (Dimensionality reduction features)，如 PCA、UMAP 。

- **layers**: 不同处理阶段的数据 。

该项目使用的数据集来源于 CZI（Chan Zuckerberg Initiative）细胞科学项目，主题为“儿童肝脏单细胞转录组图谱”，包含健康的儿童和成人肝脏组织 。

数据处理与降维 (Data Processing and Dimensionality Reduction)

- **常见处理流程**: 原始表达矩阵 -> 质量控制 (Quality Control, QC) -> 数据标准化 (Normalization) -> 对数变换 (Log transformation) -> 高变基因筛选 (Highly variable gene selection) -> 数据缩放 (Scaling) -> PCA 降维 -> 生成低维细胞向量 。

- 提供的初始数据已处理，也可在此基础上自行处理 。

- 默认降维方法为 PCA，可自行尝试自编码器 (Autoencoder)、scVI 等，但需注意效果评估 。

## 常见 ANN 算法与检索库 (Common ANN Algorithms and Retrieval Libraries)

常见 ANN 算法

- **哈希方法 (Hash-based)**: LSH 。

- **聚类索引 (Clustering Index)**: IVF (Inverted File Index) 。先聚类，再在局部区域搜索 。

- **图结构 (Graph-based)**: HNSW (Hierarchical Navigable Small World), NSG 。HNSW 基于图结构构建近邻网络，具有高召回率和高查询效率，是当前主流方法 。

- **向量压缩 (Vector Compression)**: PQ (Product Quantization), OPQ 。对向量进行压缩编码，降低内存占用，适合超大规模数据 。

- **混合结构 (Hybrid Structure)**: IVF+PQ, IVF+HNSW 。

ANN 检索库 (ANN Retrieval Libraries)

- **FAISS**: Facebook AI Research 提供的高性能向量检索库。支持 GPU 加速，支持 IVF / PQ / HNSW，适合大规模向量搜索 。

- **HNSWLIB**: 轻量级 ANN 检索库，基于 HNSW 算法。查询速度快，内存效率高，部署简单 。

- **向量数据库 (Vector Databases)**: 如 Qdrant, Milvus, Weaviate, ChromaDB。相当于 ANN 检索 + 数据库系统，支持元数据管理 (Metadata management) 。

## 开发与提交要求 (Development and Submission Requirements)

基础开发要求

- 要求为 Web 应用 (Web application) 。

- 技术栈任选（没有 web 开发经验建议使用 Flask） 。

- 系统部署要求使用 Git 进行代码版本管理 (Version control) 。

团队作业中期提交 (Mid-term Submission)

- 代码提交到 GitHub，需有提交记录 。

- 实现功能：单细胞数据读取、数据向量化表示、ANN 索引构建、相似细胞检索 。

- 至少实现一种 ANN 算法或检索库，支持 Top-K 相似细胞搜索并返回对应细胞信息 。

- 需进行 5-8 分钟的小组作业展示 。

- 评分：项目 15 分，展示 5 分。个人得分受贡献度影响 。

团队作业结项提交 (Final Submission)

- **系统功能**：完成一个可运行的单细胞 ANN 检索系统 。

- **条件检索 (Conditional retrieval)**：支持根据输入细胞和条件要求（如限定具体细胞类型）返回 Top-K 结果 。

- **实验评估 (Experimental evaluation)**：分析系统性能，如具体的响应时间等 。

- **可视化展示 (Visualization)**：提供至少一种可视化结果，鼓励交互式查询 。

- **数据集管理 (Dataset management)**：支持数据集的增加、删除及动态索引管理 。

- **提交材料**：源代码、开发文档、演示视频提交至指定链接。代码需提交 GitHub 并附带完整前后端代码、安装运行说明和注释 。

- **截止时间**：2024年7月12日24点 。

其他加分功能 (Bonus Features)

- **多数据集联合检索 (Multi-dataset combined retrieval)**：支持多个数据集联合建立索引实现跨数据集搜索 。

- **ANN 算法改进**：在检索精度/查询时间/内存占用等方面改进现有算法 。

- **RAG + 单细胞数据库**: 使用自然语言查询细胞，AI 辅助细胞分析 。

软件开发文档要求 (Software Development Document Requirements)

1.  **一、项目概述**: 开发背景、项目目标、开发环境、可行性分析、项目计划 。

2.  **二、需求分析与系统设计**: 需求分析、系统设计、详细设计、数据库设计、UI 设计 。

3.  **三、系统测试**: 测试环境、功能测试、性能测试 。

4.  **四、项目管理**: 参与人员及分工、项目进展记录、项目管理工具 。

5.  **五、用户手册 (User Manual)** 。
