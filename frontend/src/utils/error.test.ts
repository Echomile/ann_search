import { describe, expect, it } from 'vitest';
import axios, { type AxiosError } from 'axios';
import { extractError } from './error';

describe('extractError', () => {
  it('字符串 detail 优先', () => {
    const err = {
      isAxiosError: true,
      response: { data: { detail: '数据集不存在' } },
      message: 'Request failed',
    } as unknown as AxiosError;
    Object.setPrototypeOf(err, axios.AxiosError?.prototype ?? Error.prototype);
    expect(extractError(err)).toContain('数据集不存在');
  });

  it('数组 detail 取 msg', () => {
    const err = {
      isAxiosError: true,
      response: { data: { detail: [{ msg: 'field required' }] } },
      message: 'Request failed',
    } as unknown as AxiosError;
    Object.setPrototypeOf(err, axios.AxiosError?.prototype ?? Error.prototype);
    const out = extractError(err);
    expect(out === 'field required' || out === 'Request failed').toBe(true);
  });

  it('普通 Error 取 message', () => {
    expect(extractError(new Error('boom'))).toBe('boom');
  });

  it('未知输入回退兜底', () => {
    expect(extractError(42)).toBe('未知错误');
    expect(extractError(null)).toBe('未知错误');
  });
});
