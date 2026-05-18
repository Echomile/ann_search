{% extends "base.html" %}

{% block content %}
<div class="card">
    <h2>数据集管理</h2>

    <p>
        本模块用于上传和读取单细胞 AnnData 数据文件。系统会自动解析 h5ad 文件中的
        细胞数量、基因数量、元信息字段和可用于检索的向量来源。
    </p>

    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="alert {{ category }}">
                    {{ message }}
                </div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <form method="post" enctype="multipart/form-data">
        <label>上传 h5ad 数据文件：</label>
        <input type="file" name="file" accept=".h5ad">
        <button type="submit">上传并读取</button>
    </form>

    <hr>

    {% if dataset_info %}
        <h3>数据集基本信息</h3>

        <table>
            <tr>
                <th>字段</th>
                <th>内容</th>
            </tr>
            <tr>
                <td>文件名</td>
                <td>{{ dataset_info.file_name }}</td>
            </tr>
            <tr>
                <td>细胞数量</td>
                <td>{{ dataset_info.cell_count }}</td>
            </tr>
            <tr>
                <td>基因数量</td>
                <td>{{ dataset_info.gene_count }}</td>
            </tr>
            <tr>
                <td>表达矩阵形状</td>
                <td>{{ dataset_info.x_shape }}</td>
            </tr>
            <tr>
                <td>推荐向量来源</td>
                <td>{{ dataset_info.vector_source }}</td>
            </tr>
            <tr>
                <td>向量维度</td>
                <td>{{ dataset_info.vector_dim }}</td>
            </tr>
            <tr>
                <td>obs 字段</td>
                <td>{{ dataset_info.obs_columns | join(", ") }}</td>
            </tr>
            <tr>
                <td>var 字段</td>
                <td>{{ dataset_info.var_columns | join(", ") }}</td>
            </tr>
            <tr>
                <td>obsm 字段</td>
                <td>{{ dataset_info.obsm_keys | join(", ") }}</td>
            </tr>
            <tr>
                <td>layers 字段</td>
                <td>{{ dataset_info.layer_keys | join(", ") }}</td>
            </tr>
        </table>

        <h3>细胞元信息预览</h3>

        {% if dataset_info.obs_preview %}
            <table>
                <tr>
                    {% for key in dataset_info.obs_preview[0].keys() %}
                        <th>{{ key }}</th>
                    {% endfor %}
                </tr>

                {% for row in dataset_info.obs_preview %}
                    <tr>
                        {% for value in row.values() %}
                            <td>{{ value }}</td>
                        {% endfor %}
                    </tr>
                {% endfor %}
            </table>
        {% else %}
            <p>暂无细胞元信息。</p>
        {% endif %}

    {% else %}
        <h3>数据集信息</h3>
        <table>
            <tr>
                <th>数据集名称</th>
                <th>细胞数量</th>
                <th>基因数量</th>
                <th>向量维度</th>
                <th>状态</th>
            </tr>
            <tr>
                <td>暂无数据</td>
                <td>-</td>
                <td>-</td>
                <td>-</td>
                <td>未上传</td>
            </tr>
        </table>
    {% endif %}
</div>
{% endblock %}