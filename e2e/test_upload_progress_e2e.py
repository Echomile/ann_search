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

N_CELLS = 150_000
N_GENES = 400
PCA_DIM = 30
POLL_INTERVAL_S = 0.02


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
    """后台高频轮询 ``GET /datasets/{id}/upload-progress``。

    在 button click 之前启动，先轮询 ``GET /datasets`` 列表自动发现新建的
    数据集 ID（``id > max_pre``），随后立即开始对该 dataset 的
    ``/upload-progress`` 端点采样，直到 ``status in {ready, failed}``。

    用 Python ``threading`` + ``requests`` 直接打到后端，避开浏览器 fetch
    与 ``page.evaluate`` 各自的 ~30 ms 桥接开销 —— 实测能稳定捕捉到
    ``stream_to_disk`` 内 ``bytes_received > 0`` 那个 < 50 ms 的瞬时窗口。
    """

    def __init__(
        self,
        token: str,
        backend_url: str,
        max_pre_id: int,
        interval_s: float = POLL_INTERVAL_S,
    ) -> None:
        super().__init__(daemon=True)
        self.token = token
        self.backend_url = backend_url
        self.max_pre_id = max_pre_id
        self.interval_s = interval_s
        self.samples: list[dict] = []
        self.dataset_id: int | None = None
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # noqa: D401
        sess = requests.Session()
        headers = {"Authorization": f"Bearer {self.token}"}
        t0 = time.time()
        while not self._stop_event.is_set():
            try:
                if self.dataset_id is None:
                    r = sess.get(
                        f"{self.backend_url}/api/v1/datasets", headers=headers, timeout=3
                    )
                    if r.ok:
                        cand = [d["id"] for d in r.json() if d["id"] > self.max_pre_id]
                        if cand:
                            self.dataset_id = max(cand)
                if self.dataset_id is not None:
                    r = sess.get(
                        f"{self.backend_url}/api/v1/datasets/{self.dataset_id}/upload-progress",
                        headers=headers,
                        timeout=3,
                    )
                    if r.ok:
                        data = r.json()
                        data["_t"] = round(time.time() - t0, 3)
                        self.samples.append(data)
                        if data.get("status") in {"ready", "failed"}:
                            break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(self.interval_s)


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

    poller = _ProgressPoller(admin_token, BACKEND_URL, max_pre)
    poller.start()
    time.sleep(0.05)

    new_dataset_id: int | None = None
    try:
        page.get_by_role("button", name="开始上传").click()
        print("[step] 已点击 开始上传，等待 poller 终止")

        deadline = time.time() + 300
        while time.time() < deadline:
            if poller.samples and poller.samples[-1].get("status") in {"ready", "failed"}:
                break
            time.sleep(0.2)
        poller.stop()
        poller.join(timeout=2)

        samples = poller.samples
        new_dataset_id = poller.dataset_id

        seen_positive_bytes = any(
            isinstance(s.get("bytes_received"), int) and s["bytes_received"] > 0 for s in samples
        )
        seen_uploading = any(s.get("status") == "uploading" for s in samples)
        final_status = samples[-1].get("status") if samples else None
        print(
            f"[poll] dataset_id={new_dataset_id} 采样 {len(samples)} 条；"
            f"status=uploading 出现 {seen_uploading}；bytes_received>0 出现 {seen_positive_bytes}；"
            f"final={final_status}"
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
        poller.stop()
        if poller.is_alive():
            poller.join(timeout=2)
        if new_dataset_id is not None:
            _cleanup_dataset(admin_token, new_dataset_id)


if __name__ == "__main__":
    sys.exit(pytest.main(["-vv", "--tb=short", "-s", __file__]))
