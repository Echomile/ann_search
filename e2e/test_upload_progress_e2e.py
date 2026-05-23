"""数据集上传 + 双进度条 e2e 流程。

通过 ``anndata`` 在 pytest 临时目录构造一个 ~30 MB 的微型 ``.h5ad``，
预先填好 ``obsm['X_pca']`` 与 ``obsm['X_umap']`` 以跳过完整 PCA / UMAP，
然后：

1. 登录 demo，进入 ``/datasets``；
2. ``setInputFiles`` 注入文件 + 填写名称 + 点击 "开始上传"；
3. 上传触发后立即并发轮询 ``GET /datasets/{id}/upload-progress``，
   验证至少出现一次 ``bytes_received > 0``；
4. 继续轮询直到 ``status=ready``；
5. 通过后端 API 清理临时数据集。

文件刻意 >> 8 MB（一个 chunk 大小），保证 ``stream_to_disk`` 至少触发 3+
次 ``on_chunk``，前端轮询能 catch 到非零字节窗口。

运行：

* ``cd backend && uv run pytest ../e2e/test_upload_progress_e2e.py -vv``
* ``cd backend && uv run python ../e2e/test_upload_progress_e2e.py``
"""

from __future__ import annotations

import secrets
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import requests
from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "e2e"))

# fmt: off
from conftest import BACKEND_URL, backend_login_token, login_demo  # noqa: E402
# fmt: on

N_CELLS = 80_000
N_GENES = 300
PCA_DIM = 30
POLL_INTERVAL_S = 0.03


def _make_mini_h5ad(path: Path) -> int:
    """构造一个 ~30 MB 的微型 ``.h5ad``。

    预填 ``obsm['X_pca']`` 与 ``obsm['X_umap']`` 让后端预处理可以直接复用，
    避免触发完整 PCA 流程拖慢测试。禁用 HDF5 压缩以保证文件大小可控、
    跨过多个 8 MB 写盘 chunk。

    Args:
        path: 目标 ``.h5ad`` 路径。

    Returns:
        int: 生成文件的字节数。
    """
    import anndata as ad

    rng = np.random.default_rng(42)
    X = rng.standard_normal((N_CELLS, N_GENES), dtype=np.float32)
    pca = rng.standard_normal((N_CELLS, PCA_DIM), dtype=np.float32)
    umap = rng.standard_normal((N_CELLS, 2), dtype=np.float32)
    adata = ad.AnnData(X=X)
    adata.obs_names = [f"cell_{i:07d}" for i in range(N_CELLS)]
    adata.var_names = [f"gene_{j:04d}" for j in range(N_GENES)]
    adata.obsm["X_pca"] = pca
    adata.obsm["X_umap"] = umap
    adata.write_h5ad(path, compression=None)
    return path.stat().st_size


def _evaluate_dataset_ids(page: Page) -> list[int]:
    """从浏览器内拉当前用户的数据集 ID 列表。"""
    return page.evaluate(
        """async () => {
            const t = localStorage.getItem('ann_search_token');
            const r = await fetch('/api/v1/datasets', {headers: {Authorization: `Bearer ${t}`}});
            if (!r.ok) return [];
            const list = await r.json();
            return list.map(d => d.id);
        }"""
    )


def _cleanup_dataset(token: str, dataset_id: int) -> None:
    """兜底通过后端 API 删除数据集，失败仅打印。"""
    try:
        requests.delete(
            f"{BACKEND_URL}/api/v1/datasets/{dataset_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        print(f"[cleanup] 已删除临时数据集 #{dataset_id}")
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] 删除数据集 {dataset_id} 失败: {exc}")


class _ProgressPoller(threading.Thread):
    """后台线程高频轮询 ``GET /datasets/{id}/upload-progress``。

    用独立线程 + ``requests`` 直接打到后端，避免 ``page.evaluate``
    每次 ~30ms 的桥接开销，确保 ``stream_to_disk`` 内部那短暂的窗口
    （``bytes_received > 0`` 且 ``status=uploading``）能被采样到。
    """

    def __init__(self, token: str, backend_url: str, dataset_id: int) -> None:
        super().__init__(daemon=True)
        self.token = token
        self.backend_url = backend_url
        self.dataset_id = dataset_id
        self.samples: list[dict] = []
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # noqa: D401
        sess = requests.Session()
        headers = {"Authorization": f"Bearer {self.token}"}
        while not self._stop.is_set():
            try:
                r = sess.get(
                    f"{self.backend_url}/api/v1/datasets/{self.dataset_id}/upload-progress",
                    headers=headers,
                    timeout=3,
                )
                if r.ok:
                    self.samples.append(r.json())
            except Exception:  # noqa: BLE001
                pass
            time.sleep(POLL_INTERVAL_S)


def test_upload_progress_flow(page: Page, tmp_path: Path) -> None:
    """跑完上传 → 轮询 progress → ready 全流程，含清理。"""
    h5_path = tmp_path / "mini.h5ad"
    size = _make_mini_h5ad(h5_path)
    print(f"[setup] mini h5ad: {h5_path} ({size / 2**20:.1f} MB, {N_CELLS} cells x {N_GENES} genes)")

    login_demo(page)

    admin_token = backend_login_token()
    pre_ids = _evaluate_dataset_ids(page)
    max_pre = max(pre_ids) if pre_ids else 0
    print(f"[setup] 上传前 dataset id 最大值 = {max_pre}")

    name = f"upload_progress_e2e_{secrets.token_hex(3)}"
    page.get_by_placeholder("例如：pbmc3k_v2").fill(name)
    page.locator('input[type="file"]').set_input_files(str(h5_path))
    # 等 Dragger 内的文件列表渲染（mini.h5ad 文件名出现），确保 onChange 已落地
    page.wait_for_selector(f".ant-upload-list-item:has-text('mini.h5ad')", timeout=10_000)
    page.wait_for_timeout(300)
    print(f"[step] 文件已注入，开始点击 '开始上传'")

    new_dataset_id: int | None = None
    poller: _ProgressPoller | None = None
    try:
        page.get_by_role("button", name="开始上传").click()
        print("[step] 已点击 开始上传，开始轮询新 dataset_id")

        deadline = time.time() + 90
        while time.time() < deadline:
            ids = _evaluate_dataset_ids(page)
            cand = [i for i in ids if i > max_pre]
            if cand:
                new_dataset_id = max(cand)
                break
            time.sleep(0.05)
        assert new_dataset_id is not None, "上传 90s 内未观察到新 dataset_id"
        print(f"[step] 上传已开始，新 dataset_id={new_dataset_id}")

        poller = _ProgressPoller(admin_token, BACKEND_URL, new_dataset_id)
        poller.start()

        final_status: str | None = None
        deadline = time.time() + 300
        while time.time() < deadline:
            if poller.samples:
                latest = poller.samples[-1]
                if latest.get("status") in {"ready", "failed"}:
                    final_status = latest["status"]
                    break
            time.sleep(0.2)
        poller.stop()
        poller.join(timeout=2)

        samples = poller.samples
        seen_positive_bytes = any(
            isinstance(s.get("bytes_received"), int) and s["bytes_received"] > 0 for s in samples
        )
        seen_uploading = any(s.get("status") == "uploading" for s in samples)
        print(
            f"[poll] 采样 {len(samples)} 条；status=uploading 出现 {seen_uploading}；"
            f"bytes_received>0 出现 {seen_positive_bytes}"
        )
        for s in samples[:3]:
            print(f"[sample-head] {s}")
        for s in samples[-3:]:
            print(f"[sample-tail] {s}")

        assert seen_uploading, "未观察到 status=uploading；上传可能太快或失败"
        assert seen_positive_bytes, (
            f"未观察到 bytes_received > 0；采样数={len(samples)}，"
            "可能是文件太小，建议增大 N_CELLS / N_GENES。"
        )
        assert final_status == "ready", f"数据集最终状态非 ready，实际为 {final_status}"
        print(f"[done] 数据集 #{new_dataset_id} 上传 + 预处理完成")
    finally:
        if poller is not None and poller.is_alive():
            poller.stop()
            poller.join(timeout=2)
        if new_dataset_id is not None:
            _cleanup_dataset(admin_token, new_dataset_id)


if __name__ == "__main__":
    sys.exit(pytest.main(["-vv", "--tb=short", "-s", __file__]))
