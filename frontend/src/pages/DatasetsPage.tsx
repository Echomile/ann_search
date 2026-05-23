import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Popconfirm,
  Progress,
  Space,
  Table,
  Tag,
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
} from '@ant-design/icons';
import { datasetsApi } from '@/api/datasets';
import type { Dataset, DatasetStatusName } from '@/types/dataset';
import { useDatasetStore } from '@/store/datasetStore';
import { datasetStatusColor, formatDateTime } from '@/utils/format';
import { extractError } from '@/utils/error';
import { usePolling } from '@/hooks/usePolling';

const { Title, Paragraph, Text } = Typography;
const { Dragger } = Upload;

const POLL_INTERVAL_MS = 5000;
const FINAL_STATUSES: DatasetStatusName[] = ['ready', 'failed'];

interface UploadFormValues {
  name: string;
}

const DatasetsPage = () => {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [form] = Form.useForm<UploadFormValues>();
  const currentDataset = useDatasetStore((s) => s.currentDataset);
  const setCurrentDataset = useDatasetStore((s) => s.setCurrentDataset);
  const lastUploadRef = useRef<string | null>(null);

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

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

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
    try {
      const resp = await datasetsApi.upload(values.name, raw, {
        onUploadProgress: (event) => {
          if (event.total) {
            setUploadPercent(Math.round((event.loaded / event.total) * 100));
          }
        },
      });
      lastUploadRef.current = resp.dataset.name;
      message.success('上传成功，已入队预处理');
      form.resetFields();
      setFileList([]);
      setUploadPercent(0);
      await fetchAll();
    } catch (err) {
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
      render: (status: DatasetStatusName) => (
        <Tag color={datasetStatusColor(status)}>{status}</Tag>
      ),
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
        管理 .h5ad 单细胞数据集：拖拽上传后端会自动预处理；选中某行即可在索引 / 检索 / 可视化页面继续使用。
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
          {uploading && (
            <Form.Item>
              <Progress percent={uploadPercent} status="active" />
            </Form.Item>
          )}
          {lastUploadRef.current && !uploading && (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
              message={`「${lastUploadRef.current}」已入队预处理，列表将在状态变化时自动刷新。`}
            />
          )}
          <Space>
            <Button type="primary" loading={uploading} onClick={handleSubmit}>
              {uploading ? `上传中 ${uploadPercent}%` : '开始上传'}
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
        {datasets.length === 0 && !loading ? (
          <Empty description="暂无数据集，请先上传 .h5ad" />
        ) : (
          <Table<Dataset>
            rowKey="id"
            loading={loading}
            columns={columns}
            dataSource={datasets}
            pagination={{ pageSize: 10 }}
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
    </div>
  );
};

export default DatasetsPage;
