#### 数据导入
```python
import scanpy as sc

# ---------- 1. 读取数据 ----------

adata = sc.read_h5ad("liver.h5ad")

print(adata)

# 查看细胞和基因数量

print("细胞数:", adata.n_obs)

print("基因数:", adata.n_vars)
```

#### 数据字段说明


数据采用 `.h5ad` 格式存储

- `obs`：细胞（cell）层面的元数据
- `var`：基因（gene）层面的注释    
- `obsm`：降维结果（PCA/UMAP/tSNE）   
- `uns`：全局数据描述信息

**PCA降维结果在obsm下的X_pca字段**

```python
X_pca = adata.obsm["X_pca"]

print(X_pca)
print(X_pca.shape)
```


#### 可视化

```python
# 查看细胞类型

print(adata.obs["cell_type"].value_counts())


# UMAP

sc.pl.umap(

    adata,

    color=["cell_type", "disease", "AgeGroup"]

)
```

