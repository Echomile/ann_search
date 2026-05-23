import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { formatMetaValue, renderMetadataTags } from './metadata';

describe('formatMetaValue', () => {
  it('null/undefined 显示 -', () => {
    expect(formatMetaValue(null)).toBe('-');
    expect(formatMetaValue(undefined)).toBe('-');
  });
  it('对象用 JSON 化', () => {
    expect(formatMetaValue({ a: 1 })).toBe('{"a":1}');
  });
  it('其他用 String', () => {
    expect(formatMetaValue(42)).toBe('42');
    expect(formatMetaValue('hi')).toBe('hi');
    expect(formatMetaValue(true)).toBe('true');
  });
});

describe('renderMetadataTags', () => {
  it('空 metadata 显示 -', () => {
    const { container } = render(<>{renderMetadataTags({})}</>);
    expect(container.textContent).toContain('-');
  });

  it('仅展示重要字段 Tag', () => {
    const meta = { cell_type: 'hepatocyte', tissue: 'liver', extra: 'x' };
    render(<>{renderMetadataTags(meta)}</>);
    expect(screen.getByText(/cell_type: hepatocyte/)).toBeTruthy();
    expect(screen.getByText(/tissue: liver/)).toBeTruthy();
    // extra 不在重要字段，应聚合到 "+N 更多"
    expect(screen.queryByText(/extra: x/)).toBeNull();
  });

  it('超过 6 个时显示 +N 更多', () => {
    const meta = {
      cell_type: 'a',
      tissue: 'b',
      disease: 'c',
      donor_age: 'd',
      sex: 'e',
      assay: 'f',
      extra1: '1',
      extra2: '2',
    };
    render(<>{renderMetadataTags(meta)}</>);
    expect(screen.getByText(/\+2 更多/)).toBeTruthy();
  });
});
