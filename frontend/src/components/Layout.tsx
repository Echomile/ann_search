import { useMemo, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Layout as AntLayout,
  Menu,
  Button,
  Avatar,
  Dropdown,
  Space,
  Drawer,
  Grid,
  theme,
} from 'antd';
import type { MenuProps } from 'antd';
import {
  DatabaseOutlined,
  DeploymentUnitOutlined,
  SearchOutlined,
  DotChartOutlined,
  DashboardOutlined,
  MessageOutlined,
  UserOutlined,
  LogoutOutlined,
  TeamOutlined,
  MenuOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/store/authStore';

const { Header, Sider, Content } = AntLayout;
const { useBreakpoint } = Grid;

interface MenuItem {
  key: string;
  label: string;
  icon: JSX.Element;
}

const BASE_MENU_ITEMS: MenuItem[] = [
  { key: '/datasets', label: '数据集', icon: <DatabaseOutlined /> },
  { key: '/indexes', label: '索引管理', icon: <DeploymentUnitOutlined /> },
  { key: '/search', label: '检索', icon: <SearchOutlined /> },
  { key: '/visualization', label: '可视化', icon: <DotChartOutlined /> },
  { key: '/evaluation', label: '性能评测', icon: <DashboardOutlined /> },
  { key: '/rag', label: 'RAG', icon: <MessageOutlined /> },
];

// 全局布局：左侧菜单 + 顶部用户栏 + 主体 Outlet；窄屏切换为抽屉式菜单
const Layout = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const screens = useBreakpoint();
  const isMobile = screens.md === false;
  const { user, logout } = useAuthStore();
  const {
    token: { colorBgContainer, colorBorderSecondary },
  } = theme.useToken();

  const menuItems = useMemo<MenuItem[]>(
    () => [
      ...BASE_MENU_ITEMS,
      ...(user?.role === 'admin'
        ? [{ key: '/admin/users', label: '用户管理', icon: <TeamOutlined /> }]
        : []),
    ],
    [user?.role],
  );

  const selectedKey = useMemo(() => {
    const match = menuItems.find((item) => location.pathname.startsWith(item.key));
    return match ? match.key : '/datasets';
  }, [location.pathname, menuItems]);

  const handleLogout = () => {
    logout();
    navigate('/login', { replace: true });
  };

  const handleMenuClick = (key: string) => {
    navigate(key);
    setDrawerOpen(false);
  };

  const userMenu: MenuProps['items'] = [
    {
      key: 'logout',
      label: '退出登录',
      icon: <LogoutOutlined />,
      onClick: handleLogout,
    },
  ];

  // 共享菜单组件，宽屏在 Sider 内、窄屏在 Drawer 内
  const renderMenu = (themeMode: 'dark' | 'light') => (
    <Menu
      theme={themeMode}
      mode="inline"
      selectedKeys={[selectedKey]}
      items={menuItems.map(({ key, label, icon }) => ({ key, label, icon }))}
      onClick={({ key }) => handleMenuClick(key)}
    />
  );

  return (
    <AntLayout style={{ minHeight: '100vh' }}>
      {!isMobile && (
        <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} theme="dark" width={220}>
          <div
            style={{
              height: 56,
              margin: 12,
              color: '#fff',
              fontWeight: 600,
              fontSize: collapsed ? 14 : 16,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'rgba(255,255,255,0.08)',
              borderRadius: 6,
            }}
          >
            {collapsed ? 'ANN' : '单细胞 ANN 检索'}
          </div>
          {renderMenu('dark')}
        </Sider>
      )}
      <Drawer
        title="单细胞 ANN 检索"
        placement="left"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        styles={{ body: { padding: 0 } }}
        width={240}
      >
        {renderMenu('light')}
      </Drawer>
      <AntLayout>
        <Header
          style={{
            padding: isMobile ? '0 12px' : '0 24px',
            background: colorBgContainer,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: `1px solid ${colorBorderSecondary}`,
          }}
        >
          <Space size={12}>
            {isMobile && (
              <Button
                type="text"
                icon={<MenuOutlined />}
                onClick={() => setDrawerOpen(true)}
                aria-label="打开菜单"
              />
            )}
            <div style={{ fontSize: isMobile ? 15 : 18, fontWeight: 600 }}>
              单细胞 ANN 检索系统
            </div>
          </Space>
          <Dropdown menu={{ items: userMenu }} placement="bottomRight">
            <Space style={{ cursor: 'pointer' }}>
              <Avatar size="small" icon={<UserOutlined />} />
              {!isMobile && <span>{user?.username ?? '未登录'}</span>}
              <Button type="link" size="small" onClick={handleLogout}>
                登出
              </Button>
            </Space>
          </Dropdown>
        </Header>
        <Content
          style={{
            margin: isMobile ? 12 : 24,
            padding: isMobile ? 12 : 24,
            background: colorBgContainer,
            borderRadius: 8,
          }}
        >
          <Outlet />
        </Content>
      </AntLayout>
    </AntLayout>
  );
};

export default Layout;
