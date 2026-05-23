import type { ReactNode } from 'react';
import { Descriptions, Popover, Space, Tag, Typography } from 'antd';

const { Text } = Typography;

/**
 * Metadata 折叠展示重要字段优先级。
 *
 * 顺序即展示顺序，最多取前 6 个非空字段；
 * 数据集多达 56 个 Tag 时这一裁剪能显著降低视觉负担。
 */
const IMPORTANT_FIELDS: readonly string[] = [
  'cell_type',
  'tissue',
  'disease',
  'donor_age',
  'sex',
  'assay',
];

const MAX_VISIBLE_FIELDS = 6;

/** 将任意值规整为字符串以渲染：null/undefined 显示为 -，对象做 JSON 化 */
export const formatMetaValue = (v: unknown): string => {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
};

const isPresent = (v: unknown): boolean => v !== null && v !== undefined && v !== '';

/**
 * 渲染折叠后的 metadata Tag 列表。
 *
 * 默认只展示 ``IMPORTANT_FIELDS`` 中存在且非空的字段（最多 6 个），
 * 当字段总数大于已展示数时，末尾追加 "+N 更多" Tag；
 * 点击后通过 Antd Popover 弹出完整 ``Descriptions`` 视图，避免占满整行。
 *
 * @param meta - 任意 metadata 字典；可为 null/undefined。
 * @returns ReactNode 可直接放在 Table 列 / Card 中。
 */
export const renderMetadataTags = (
  meta: Record<string, unknown> | null | undefined,
): ReactNode => {
  const entries = Object.entries(meta ?? {});
  if (entries.length === 0) return <Text type="secondary">-</Text>;

  const importantEntries: [string, unknown][] = [];
  for (const key of IMPORTANT_FIELDS) {
    if (importantEntries.length >= MAX_VISIBLE_FIELDS) break;
    const value = (meta as Record<string, unknown> | null | undefined)?.[key];
    if (isPresent(value)) importantEntries.push([key, value]);
  }

  const remaining = entries.length - importantEntries.length;

  const fullList = (
    <div style={{ maxWidth: 480, maxHeight: 360, overflowY: 'auto' }}>
      <Descriptions
        size="small"
        column={1}
        bordered
        items={entries.map(([k, v]) => ({
          key: k,
          label: k,
          children: formatMetaValue(v),
        }))}
      />
    </div>
  );

  return (
    <Space size={[4, 4]} wrap>
      {importantEntries.map(([k, v]) => (
        <Tag key={k} color="geekblue">{`${k}: ${formatMetaValue(v)}`}</Tag>
      ))}
      {remaining > 0 && (
        <Popover
          content={fullList}
          title={`完整 Metadata（共 ${entries.length} 项）`}
          trigger="click"
          placement="leftTop"
        >
          <Tag style={{ cursor: 'pointer' }} color="default">{`+${remaining} 更多`}</Tag>
        </Popover>
      )}
    </Space>
  );
};
