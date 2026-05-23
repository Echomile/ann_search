// 管理员-用户管理页：CRUD + 切换角色 + 重置密码

import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { CopyOutlined, KeyOutlined, ReloadOutlined } from '@ant-design/icons';
import { adminApi } from '@/api/auth';
import { useAuthStore } from '@/store/authStore';
import type { AdminUser } from '@/types/admin';
import { formatDateTime } from '@/utils/format';
import { extractError } from '@/utils/error';

const { Title, Paragraph, Text } = Typography;

const roleColor = (role: AdminUser['role']) => (role === 'admin' ? 'red' : 'blue');

const AdminUsersPage = () => {
  const currentUser = useAuthStore((s) => s.user);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [tempPasswordTarget, setTempPasswordTarget] = useState<{
    username: string;
    password: string;
  } | null>(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const list = await adminApi.listUsers();
      setUsers(list);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  const handleRoleChange = async (id: number, role: AdminUser['role']) => {
    setPendingId(id);
    try {
      const next = await adminApi.updateRole(id, role);
      setUsers((prev) => prev.map((u) => (u.id === id ? next : u)));
      message.success(`已将 #${id} 角色更新为 ${role}`);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setPendingId(null);
    }
  };

  const handleDelete = async (record: AdminUser) => {
    setPendingId(record.id);
    try {
      await adminApi.deleteUser(record.id);
      setUsers((prev) => prev.filter((u) => u.id !== record.id));
      message.success(`已删除用户「${record.username}」`);
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setPendingId(null);
    }
  };

  const handleResetPassword = async (record: AdminUser) => {
    setPendingId(record.id);
    try {
      const resp = await adminApi.resetPassword(record.id);
      setTempPasswordTarget({ username: record.username, password: resp.temp_password });
    } catch (err) {
      message.error(extractError(err));
    } finally {
      setPendingId(null);
    }
  };

  const handleCopyPassword = async () => {
    if (!tempPasswordTarget) return;
    try {
      await navigator.clipboard.writeText(tempPasswordTarget.password);
      message.success('已复制到剪贴板');
    } catch {
      message.warning('复制失败，请手动选中复制');
    }
  };

  const columns: ColumnsType<AdminUser> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 80 },
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      render: (v: string, record) => (
        <Space>
          <Text strong>{v}</Text>
          {currentUser?.id === record.id && <Tag color="gold">我</Tag>}
        </Space>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      width: 120,
      render: (role: AdminUser['role']) => <Tag color={roleColor(role)}>{role}</Tag>,
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
      width: 360,
      render: (_: unknown, record) => {
        const isSelf = currentUser?.id === record.id;
        return (
          <Space size="small">
            <Select<AdminUser['role']>
              size="small"
              value={record.role}
              style={{ width: 110 }}
              disabled={isSelf || pendingId === record.id}
              onChange={(v) => handleRoleChange(record.id, v)}
              options={[
                { value: 'user', label: 'user' },
                { value: 'admin', label: 'admin' },
              ]}
            />
            <Button
              size="small"
              icon={<KeyOutlined />}
              loading={pendingId === record.id}
              onClick={() => handleResetPassword(record)}
            >
              重置密码
            </Button>
            <Popconfirm
              title={`删除用户「${record.username}」？`}
              description="该用户的全部数据集、索引、检索记录都会被级联清理。"
              okType="danger"
              disabled={isSelf}
              onConfirm={() => handleDelete(record)}
            >
              <Button
                danger
                size="small"
                disabled={isSelf}
                loading={pendingId === record.id}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  return (
    <div>
      <Title level={3}>用户管理</Title>
      <Paragraph type="secondary">
        管理员视图：可切换用户角色、重置密码（生成一次性明文）或删除用户。
        删除会通过外键级联清理其名下数据集 / 索引 / 检索记录及对应磁盘文件，操作不可撤销。
      </Paragraph>

      <Card
        title="全部用户"
        extra={
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => fetchAll()}>
            刷新
          </Button>
        }
      >
        <Table<AdminUser>
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={users}
          pagination={{ pageSize: 20 }}
        />
      </Card>

      <Modal
        open={tempPasswordTarget !== null}
        title="一次性临时密码"
        onCancel={() => setTempPasswordTarget(null)}
        footer={
          <Space>
            <Button icon={<CopyOutlined />} onClick={handleCopyPassword}>
              复制密码
            </Button>
            <Button type="primary" onClick={() => setTempPasswordTarget(null)}>
              我已记下
            </Button>
          </Space>
        }
      >
        {tempPasswordTarget && (
          <>
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="该密码只会显示一次，请立即复制并安全转交给用户。"
            />
            <Paragraph>
              用户：<Text strong>{tempPasswordTarget.username}</Text>
            </Paragraph>
            <Paragraph copyable={{ text: tempPasswordTarget.password }}>
              <Text code style={{ fontSize: 16 }}>
                {tempPasswordTarget.password}
              </Text>
            </Paragraph>
          </>
        )}
      </Modal>
    </div>
  );
};

export default AdminUsersPage;
