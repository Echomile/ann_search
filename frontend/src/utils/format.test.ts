import { describe, expect, it } from 'vitest';
import {
  datasetStatusColor,
  formatBytes,
  formatDateTime,
  formatDuration,
  formatMemoryMb,
  formatSeconds,
  indexStatusColor,
} from './format';

describe('formatBytes', () => {
  it('null/undefined/NaN -> "-"', () => {
    expect(formatBytes(null)).toBe('-');
    expect(formatBytes(undefined)).toBe('-');
    expect(formatBytes(NaN)).toBe('-');
  });
  it('0 byte 直接返回 0 B', () => {
    expect(formatBytes(0)).toBe('0 B');
  });
  it('阶梯换算 B → KB → MB → GB', () => {
    expect(formatBytes(1024)).toBe('1.00 KB');
    expect(formatBytes(1024 * 1024)).toBe('1.00 MB');
    expect(formatBytes(1.5 * 1024 ** 3)).toBe('1.50 GB');
  });
  it('小数位可配置', () => {
    expect(formatBytes(2048, 0)).toBe('2 KB');
    expect(formatBytes(2048, 3)).toBe('2.000 KB');
  });
});

describe('formatDuration', () => {
  it('null/undefined/NaN -> "-"', () => {
    expect(formatDuration(null)).toBe('-');
    expect(formatDuration(NaN)).toBe('-');
  });
  it('sub-ms / ms / s / min 四段', () => {
    expect(formatDuration(0.42)).toContain('0.42 ms');
    expect(formatDuration(750)).toContain('750.0 ms');
    expect(formatDuration(45_000)).toContain('45.00 s');
    expect(formatDuration(125_000)).toBe('2 min 5.0 s');
  });
});

describe('formatSeconds / formatMemoryMb', () => {
  it('formatSeconds 走 formatDuration', () => {
    expect(formatSeconds(0.5)).toBe(formatDuration(500));
    expect(formatSeconds(null)).toBe('-');
  });
  it('formatMemoryMb MB → GB 阶梯', () => {
    expect(formatMemoryMb(512)).toBe('512.00 MB');
    expect(formatMemoryMb(2048)).toBe('2.00 GB');
    expect(formatMemoryMb(null)).toBe('-');
  });
});

describe('formatDateTime', () => {
  it('空值 -> "-"', () => {
    expect(formatDateTime(null)).toBe('-');
    expect(formatDateTime('')).toBe('-');
  });
  it('非法 ISO 原样返回', () => {
    expect(formatDateTime('not-a-date')).toBe('not-a-date');
  });
  it('合法 ISO 转 locale 字符串', () => {
    const out = formatDateTime('2026-05-23T15:30:00Z');
    expect(out).not.toBe('-');
    expect(out.length).toBeGreaterThan(5);
  });
});

describe('status color helpers', () => {
  it('dataset 状态映射', () => {
    expect(datasetStatusColor('ready')).toBe('green');
    expect(datasetStatusColor('uploading')).toBe('blue');
    expect(datasetStatusColor('preprocessing')).toBe('gold');
    expect(datasetStatusColor('failed')).toBe('red');
    expect(datasetStatusColor('unknown_xyz')).toBe('default');
  });
  it('index 状态映射', () => {
    expect(indexStatusColor('ready')).toBe('green');
    expect(indexStatusColor('building')).toBe('gold');
    expect(indexStatusColor('failed')).toBe('red');
    expect(indexStatusColor('xxx')).toBe('default');
  });
});
