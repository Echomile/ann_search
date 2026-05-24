import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Skeleton,
  Space,
  Steps,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { UploadFile, UploadProps } from 'antd/es/upload/interface';
import {
  ClearOutlined,
  DeleteOutlined,
  InboxOutlined,
  ReloadOutlined,
  CheckCircleTwoTone,
  LinkOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import { alignmentApi, datasetsApi } from '@/api/datasets';
import type { Dataset, DatasetStatusName, UploadProgressResponse } from '@/types/dataset';
import type { AlignMethod, AlignedDataset } from '@/types/aligned';
import { Link, useNavigate } from 'react-router-dom';
import { useDatasetStore } from '@/store/datasetStore';
import { datasetStatusColor, formatDateTime } from '@/utils/format';
import { extractError } from '@/utils/error';
import { usePolling } from '@/hooks/usePolling';

const { Title, Paragraph, Text } = Typography;
const { Dragger } = Upload;

const POLL_INTERVAL_MS = 5000;
const POLL_UPLOAD_PROGRESS_MS = 500;
const FINAL_STATUSES: DatasetStatusName[] = ['ready', 'failed'];

// 上传流水线阶段：transfer=axios 字节传输；writing=后端写盘；preprocessing=Scanpy；done=终态
type UploadPhase = 'idle' | 'transfer' | 'writing' | 'preprocessing' | 'done';

const PHASE_TO_STEP_INDEX: Record<UploadPhase, number> = {
  idle: 0,
  transfer: 0,
  writing: 1,
  preprocessing: 2,
  done: 3,
};

interface UploadFormValues {
  name: string;
}

/**
 * 渲染"开始上传"按钮文案，根据当前阶段动态切换。
 *
 * 在 `transfer` 阶段显示 axios 字节传输百分比；
 * 在 `writing` 阶段显示后端写盘百分比（``total_bytes`` 缺失时退化为"写盘中"）；
 * 在 `preprocessing` 阶段显示固定文案；其他阶段回落到"开始上传"。
 */
const renderSubmitLabel = (
  uploading: boolean,
  phase: UploadPhase,
  percent: number,
  backend: UploadProgressResponse | null,
): string => {
  if (!uploading) return '开始上传';
  if (phase === 'transfer') return `前端上传中 ${percent}%`;
  if (phase === 'writing') {
    if (backend?.percent != null) return `后端写盘中 ${backend.percent.toFixed(1)}%`;
    return '后端写盘中…';
  }
  if (phase === 'preprocessing') return 'Scanpy 预处理中…';
  return '处理中…';
};

/**
 * 渲染后端进度条的 ``label`` 文案。
 */
const renderBackendLabel = (phase: UploadPhase, backend: UploadProgressResponse | null): string => {
  if (phase === 'preprocessing') return 'Scanpy 预处理中（PCA / UMAP）';
  if (phase === 'done') return '后端处理完成';
  if (backend?.total_bytes != null) return '后端写盘进度（bytes_received / total_bytes）';
  return '后端写盘中（streaming，进度不可知）';
};

/**
 * 根据阶段渲染对应的后端进度条 / spinner。
 *
 * - ``writing`` + ``total_bytes`` 已知：百分比进度条；
 * - ``writing`` + ``total_bytes=null``：indeterminate active 进度条；
 * - ``preprocessing``：indeterminate active + Loading 图标；
 * - ``done``：100% 成功 / 异常状态。
 */
const renderBackendProgress = (
  phase: UploadPhase,
  backend: UploadProgressResponse | null,
  hasError: boolean,
): JSX.Element => {
  if (phase === 'done') {
    return <Progress percent={100} status={hasError ? 'exception' : 'success'} />;
  }
  if (phase === 'preprocessing') {
    return (
      <Space>
        <LoadingOutlined spin />
        <Progress percent={100} status="active" showInfo={false} style={{ width: 320 }} />
        <Text type="secondary">不可知耗时，请耐心等待</Text>
      </Space>
    );
  }
  // writing
  if (backend?.total_bytes != null && backend.percent != null) {
    return <Progress percent={backend.percent} status="active" />;
  }
  return (
    <Space>
      <LoadingOutlined spin />
      <Progress percent={100} status="active" showInfo={false} style={{ width: 320 }} />
      <Text type="secondary">streaming 上传，未知总大小</Text>
    </Space>
  );
};

const DatasetsPage = () => {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [uploadPhase, setUploadPhase] = useState<UploadPhase>('idle');
  const [backendProgress, setBackendProgress] = useState<UploadProgressResponse | null>(null);
  const [uploadHasError, setUploadHasError] = useState(false);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [form] = Form.useForm<UploadFormValues>();
  const currentDataset = useDatasetStore((s) => s.currentDataset);
  const setCurrentDataset = useDatasetStore((s) => s.setCurrentDataset);
  const navigate = useNavigate();

  // D7: 跨数据集对齐相关 state
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);
  const [aligning, setAligning] = useState(false);
  const [alignModalOpen, setAlignModalOpen] = useState(false);
  const [alignedList, setAlignedList] = useState<AlignedDataset[]>([]);
  const [alignForm] = Form.useForm<{
    name?: string;
    method: AlignMethod;
    target_dim: number;
  }>();
  const lastUploadRef = useRef<string | null>(null);
  // 轮询取消标记：组件卸载或下次上传开始时置 true，避免脏 setState
  const pollAbortRef = useRef<{ cancelled: boolean }>({ cancelled: false });

  useEffect(() => {
    const token = pollAbortRef.current;
    return () => {
      token.cancelled = true;
    };
  }, []);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const list = await datasetsApi.list();
      setDatasets(list);
      if (currentDataset) {
        const matched = list.find((d) => d.id === currentDataset.id);
        if (matched && matched.status !== currentDataset.status) {
          setCurrentDataset(matched);
        } else if (!matched) {
          setCurrentDataset(null);
        }
      }
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setLoading(false);
    }
  }, [currentDataset, setCurrentDataset]);

  // D7: 加载已存在的对齐数据集，用于"已对齐"区域展示
  const fetchAlignedList = useCallback(async () => {
    try {
      const list = await alignmentApi.list();
      setAlignedList(list);
    } catch {
      setAlignedList([]);
    }
  }, []);

  // D7: 提交对齐请求（同步，等待后端完成 PCA / harmony 后跳转 search 页）
  const handleAlignSubmit = useCallback(async () => {
    const values = await alignForm.validateFields().catch(() => null);
    if (!values) return;
    if (selectedRowKeys.length < 2) {
      message.error('至少选择 2 个数据集再触发对齐');
      return;
    }
    setAligning(true);
    try {
      const aligned = await alignmentApi.align({
        source_dataset_ids: selectedRowKeys,
        method: values.method,
        target_dim: values.target_dim,
        name: values.name?.trim() || null,
      });
      message.success(
        `对齐完成 #${aligned.id}，实际方法 ${aligned.method}，已切换到检索页`,
      );
      setAlignModalOpen(false);
      setSelectedRowKeys([]);
      void fetchAlignedList();
      // 跳转到 search 页，便于立即试用对齐路径
      navigate(`/search?aligned_dataset_id=${aligned.id}`);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setAligning(false);
    }
  }, [alignForm, selectedRowKeys, fetchAlignedList, navigate]);

  // D7: 删除对齐数据集
  const handleDeleteAligned = useCallback(
    async (id: number) => {
      try {
        await alignmentApi.remove(id);
        message.success(`对齐数据集 #${id} 已删除`);
        void fetchAlignedList();
      } catch (err) {
        message.error(extractError(err));
      }
    },
    [fetchAlignedList],
  );

  useEffect(() => {
    void fetchAll();
    void fetchAlignedList();
  }, [fetchAll, fetchAlignedList]);

  const pendingIds = useMemo(
    () => datasets.filter((d) => !FINAL_STATUSES.includes(d.status)).map((d) => d.id),
    [datasets],
  );

  const refreshPending = useCallback(async () => {
    if (pendingIds.length === 0) return;
    try {
      const updates = await Promise.all(pendingIds.map((id) => datasetsApi.status(id)));
      setDatasets((prev) =>
        prev.map((ds) => {
          const u = updates.find((it) => it.dataset_id === ds.id);
          if (!u) return ds;
          return {
            ...ds,
            status: u.status,
            cell_count: u.cell_count,
            vector_dim: u.vector_dim,
            vector_source: u.vector_source,
            meta_columns: u.meta_columns,
          };
        }),
      );
    } catch {
      // 单次轮询失败忽略
    }
  }, [pendingIds]);

  usePolling(refreshPending, {
    interval: POLL_INTERVAL_MS,
    enabled: pendingIds.length > 0,
  });

  const handleRefreshOne = async (id: number) => {
    try {
      const s = await datasetsApi.status(id);
      setDatasets((prev) =>
        prev.map((ds) =>
          ds.id === id
            ? {
                ...ds,
                status: s.status,
                cell_count: s.cell_count,
                vector_dim: s.vector_dim,
                vector_source: s.vector_source,
                meta_columns: s.meta_columns,
              }
            : ds,
        ),
      );
      message.success(`数据集 #${id} 状态: ${s.status}`);
    } catch (err) {
      message.error(extractError(err));
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await datasetsApi.remove(id);
      message.success('删除成功');
      if (currentDataset?.id === id) setCurrentDataset(null);
      setDatasets((prev) => prev.filter((ds) => ds.id !== id));
    } catch (err) {
      message.error(extractError(err));
    }
  };

  const handleCleanupOrphan = async () => {
    try {
      const resp = await datasetsApi.cleanupOrphan();
      if (resp.count === 0) {
        message.info('没有需要清理的失败数据集');
      } else {
        message.success(`已清理 ${resp.count} 个失败数据集：${resp.deleted_ids.join(', ')}`);
        if (currentDataset && resp.deleted_ids.includes(currentDataset.id)) {
          setCurrentDataset(null);
        }
      }
      await fetchAll();
    } catch (err) {
      message.error(extractError(err));
    }
  };

  /**
   * 上传完成后轮询后端 ``/upload-progress``，直到 ready / failed。
   *
   * Args:
   *   datasetId: 后端返回的数据集 ID。
   *   token: 取消标记，用于组件卸载或新一轮上传开始时打断轮询。
   *
   * Returns:
   *   Promise 解析为最终阶段 (status=ready | failed) 的进度对象。
   */
  const pollUploadProgress = useCallback(
    (datasetId: number, token: { cancelled: boolean }): Promise<UploadProgressResponse> =>
      new Promise((resolve, reject) => {
        const tick = async () => {
          if (token.cancelled) {
            reject(new Error('已取消'));
            return;
          }
          try {
            const p = await datasetsApi.uploadProgress(datasetId);
            setBackendProgress(p);
            if (p.status === 'uploading') {
              setUploadPhase('writing');
            } else if (p.status === 'preprocessing') {
              setUploadPhase('preprocessing');
            }
            if (p.status === 'ready' || p.status === 'failed') {
              resolve(p);
              return;
            }
            window.setTimeout(tick, POLL_UPLOAD_PROGRESS_MS);
          } catch (e) {
            reject(e);
          }
        };
        void tick();
      }),
    [],
  );

  const handleSubmit = async () => {
    if (fileList.length === 0) {
      message.warning('请选择 .h5ad 文件');
      return;
    }
    const raw = fileList[0]?.originFileObj;
    if (!raw) {
      message.warning('文件已失效，请重新选择');
      return;
    }
    let values: UploadFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }

    setUploading(true);
    setUploadPercent(0);
    setBackendProgress(null);
    setUploadHasError(false);
    setUploadPhase('transfer');
    pollAbortRef.current = { cancelled: false };
    const abortToken = pollAbortRef.current;

    try {
      const resp = await datasetsApi.upload(values.name, raw, {
        onUploadProgress: (event) => {
          if (event.total) {
            setUploadPercent(Math.round((event.loaded / event.total) * 100));
          }
        },
      });
      // axios 完成 → POST 响应已返回，进入后端处理阶段
      setUploadPercent(100);
      setUploadPhase('writing');

      const final = await pollUploadProgress(resp.dataset.id, abortToken);

      if (final.status === 'failed') {
        setUploadHasError(true);
        setUploadPhase('done');
        message.error(`数据集「${resp.dataset.name}」预处理失败`);
      } else {
        setUploadPhase('done');
        lastUploadRef.current = resp.dataset.name;
        message.success('上传 + 预处理完成');
        form.resetFields();
        setFileList([]);
      }
      await fetchAll();
    } catch (err) {
      setUploadHasError(true);
      setUploadPhase('done');
      message.error(extractError(err));
    } finally {
      setUploading(false);
    }
  };

  const uploadProps: UploadProps = {
    accept: '.h5ad',
    multiple: false,
    maxCount: 1,
    fileList,
    beforeUpload: (file) => {
      if (!file.name.toLowerCase().endsWith('.h5ad')) {
        message.error('仅支持 .h5ad 文件');
        return Upload.LIST_IGNORE;
      }
      return false;
    },
    onChange: ({ fileList: list }) => setFileList(list.slice(-1)),
    onRemove: () => setFileList([]),
  };

  const columns: ColumnsType<Dataset> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (val: string, record) => (
        <Space>
          <Text strong>{val}</Text>
          {currentDataset?.id === record.id && (
            <CheckCircleTwoTone twoToneColor="#52c41a" title="当前选中" />
          )}
        </Space>
      ),
    },
    {
      title: '细胞数',
      dataIndex: 'cell_count',
      key: 'cell_count',
      width: 110,
      render: (v: number | null) => (v ?? '-').toLocaleString?.() ?? '-',
    },
    { title: '向量维度', dataIndex: 'vector_dim', key: 'vector_dim', width: 100 },
    { title: '向量来源', dataIndex: 'vector_source', key: 'vector_source', width: 120 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 140,
      render: (status: DatasetStatusName) => <Tag color={datasetStatusColor(status)}>{status}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (v: string) => formatDateTime(v),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_: unknown, record) => (
        <Space size="small">
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              void handleRefreshOne(record.id);
            }}
          >
            状态
          </Button>
          <Popconfirm
            title={`删除数据集「${record.name}」？`}
            okType="danger"
            onConfirm={() => handleDelete(record.id)}
            onCancel={(e) => e?.stopPropagation()}
          >
            <Button
              danger
              size="small"
              icon={<DeleteOutlined />}
              onClick={(e) => e.stopPropagation()}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={3}>数据集</Title>
      <Paragraph type="secondary">
        管理 .h5ad 单细胞数据集：拖拽上传后端会自动预处理；选中某行即可在索引 / 检索 /
        可视化页面继续使用。
      </Paragraph>

      <Card title="上传数据集" style={{ marginBottom: 24 }}>
        <Form form={form} layout="vertical">
          <Form.Item
            label="数据集名称"
            name="name"
            rules={[{ required: true, message: '请输入数据集名称' }]}
          >
            <Input placeholder="例如：pbmc3k_v2" maxLength={120} />
          </Form.Item>
          <Form.Item label="文件（.h5ad）" required>
            <Dragger {...uploadProps} disabled={uploading}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽 .h5ad 文件到此处</p>
              <p className="ant-upload-hint">单文件上传，最大支持后端配置的体积上限</p>
            </Dragger>
          </Form.Item>
          {(uploading || uploadPhase === 'done') && (
            <>
              <Form.Item>
                <Steps
                  size="small"
                  current={PHASE_TO_STEP_INDEX[uploadPhase]}
                  status={uploadHasError ? 'error' : uploadPhase === 'done' ? 'finish' : 'process'}
                  items={[
                    { title: '前端上传' },
                    { title: '后端写盘' },
                    { title: 'Scanpy 预处理' },
                    { title: '完成' },
                  ]}
                />
              </Form.Item>
              <Form.Item label="前端 → 后端字节传输">
                <Progress
                  percent={uploadPercent}
                  status={
                    uploadHasError && uploadPhase === 'transfer'
                      ? 'exception'
                      : uploadPhase === 'transfer'
                        ? 'active'
                        : 'success'
                  }
                />
              </Form.Item>
              {uploadPhase !== 'transfer' && (
                <Form.Item label={renderBackendLabel(uploadPhase, backendProgress)}>
                  {renderBackendProgress(uploadPhase, backendProgress, uploadHasError)}
                </Form.Item>
              )}
            </>
          )}
          {lastUploadRef.current && !uploading && uploadPhase === 'idle' && (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
              message={`「${lastUploadRef.current}」已上传并完成预处理。`}
            />
          )}
          <Space>
            <Button type="primary" loading={uploading} onClick={handleSubmit}>
              {renderSubmitLabel(uploading, uploadPhase, uploadPercent, backendProgress)}
            </Button>
            <Button onClick={() => fetchAll()} disabled={loading}>
              刷新列表
            </Button>
          </Space>
        </Form>
      </Card>

      <Card
        title="我的数据集"
        extra={
          <Space>
            <Tooltip
              title={
                selectedRowKeys.length < 2
                  ? '至少选择 2 个数据集 (status=ready) 才能触发跨数据集对齐'
                  : `对所选 ${selectedRowKeys.length} 个数据集做语义对齐`
              }
            >
              <Button
                type="primary"
                icon={<LinkOutlined />}
                disabled={selectedRowKeys.length < 2}
                onClick={() => {
                  alignForm.setFieldsValue({
                    method: 'intersect_only',
                    target_dim: 30,
                    name: '',
                  });
                  setAlignModalOpen(true);
                }}
              >
                对齐 ({selectedRowKeys.length})
              </Button>
            </Tooltip>
            <Popconfirm
              title="清理失败数据集？"
              description="将删除当前用户名下所有 status=failed 或缺失向量文件的数据集（含磁盘）。"
              okType="danger"
              onConfirm={handleCleanupOrphan}
            >
              <Button danger icon={<ClearOutlined />}>
                清理失败
              </Button>
            </Popconfirm>
            <Button icon={<ReloadOutlined />} onClick={() => fetchAll()} loading={loading}>
              刷新
            </Button>
          </Space>
        }
      >
        {loading && datasets.length === 0 ? (
          <Skeleton active paragraph={{ rows: 5 }} />
        ) : datasets.length === 0 ? (
          <Empty description="暂无数据集，请先上传 .h5ad" />
        ) : (
          <Table<Dataset>
            rowKey="id"
            columns={columns}
            dataSource={datasets}
            rowSelection={{
              selectedRowKeys,
              onChange: (keys) => setSelectedRowKeys(keys as number[]),
              getCheckboxProps: (record) => ({
                disabled: record.status !== 'ready',
                title:
                  record.status !== 'ready'
                    ? '只有 status=ready 的数据集可参与对齐'
                    : undefined,
              }),
            }}
            pagination={{ pageSize: 10 }}
            locale={{
              emptyText: loading ? (
                <Skeleton active paragraph={{ rows: 5 }} />
              ) : (
                <Empty description="暂无数据集，请先上传 .h5ad" />
              ),
            }}
            onRow={(record) => ({
              onClick: () => setCurrentDataset(record),
              style: { cursor: 'pointer' },
            })}
            rowClassName={(record) =>
              currentDataset?.id === record.id ? 'ant-table-row-selected' : ''
            }
          />
        )}
      </Card>

      <Card
        title="对齐数据集 (D7)"
        size="small"
        extra={
          <Button size="small" onClick={() => fetchAlignedList()}>
            刷新
          </Button>
        }
        style={{ marginTop: 16 }}
      >
        {alignedList.length === 0 ? (
          <Empty description="尚无对齐数据集；勾选 ≥2 个 ready 数据集后点击「对齐」即可" />
        ) : (
          <Table<AlignedDataset>
            rowKey="id"
            size="small"
            pagination={{ pageSize: 5 }}
            dataSource={alignedList}
            columns={[
              { title: 'ID', dataIndex: 'id', width: 60 },
              { title: '名称', dataIndex: 'name' },
              {
                title: '方法',
                dataIndex: 'method',
                width: 120,
                render: (m: string) => <Tag color="blue">{m}</Tag>,
              },
              {
                title: '维度',
                dataIndex: 'target_dim',
                width: 70,
              },
              {
                title: '细胞数',
                dataIndex: 'cell_count',
                width: 90,
              },
              {
                title: '基因交集',
                dataIndex: 'common_genes_count',
                width: 90,
              },
              {
                title: '状态',
                dataIndex: 'status',
                width: 90,
                render: (s: string) => (
                  <Tag color={s === 'done' ? 'success' : s === 'failed' ? 'error' : 'processing'}>
                    {s}
                  </Tag>
                ),
              },
              {
                title: '操作',
                width: 160,
                render: (_: unknown, row: AlignedDataset) => (
                  <Space>
                    <Link to={`/search?aligned_dataset_id=${row.id}`}>跨库检索</Link>
                    <Popconfirm
                      title={`确认删除对齐数据集 #${row.id}?`}
                      okType="danger"
                      onConfirm={() => handleDeleteAligned(row.id)}
                    >
                      <Button danger size="small" icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>

      <Modal
        title="跨数据集语义对齐"
        open={alignModalOpen}
        onCancel={() => setAlignModalOpen(false)}
        onOk={handleAlignSubmit}
        confirmLoading={aligning}
        okText="开始对齐"
        cancelText="取消"
        destroyOnClose
      >
        <Alert
          type="info"
          style={{ marginBottom: 12 }}
          showIcon
          message={`将对 ${selectedRowKeys.length} 个数据集做基因集对齐：[${selectedRowKeys.join(', ')}]`}
        />
        <Form
          form={alignForm}
          layout="vertical"
          initialValues={{ method: 'intersect_only', target_dim: 30 }}
        >
          <Form.Item label="对齐方法" name="method" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'intersect_only', label: 'intersect_only · 基因交集 + 统一 PCA' },
                {
                  value: 'harmony',
                  label: 'harmony · 在 intersect 之上跑 batch correction (需 harmonypy)',
                },
              ]}
            />
          </Form.Item>
          <Form.Item label="对齐维度 target_dim" name="target_dim" rules={[{ required: true }]}>
            <InputNumber min={2} max={512} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="对齐数据集名称（可选）" name="name">
            <Input placeholder="不填则按 aligned-{method}-{ids} 自动生成" allowClear />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default DatasetsPage;
