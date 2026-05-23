import axios from 'axios';

/**
 * 提取错误对象的可读信息，兼容 axios、原生 Error 与 FastAPI 422 校验错误。
 *
 * 优先级：
 * 1. axios 错误：`response.data.detail` 为字符串时直接返回；
 *    若为 FastAPI 校验错误数组（含 `msg` 字段），取第一项的 `msg`；否则回退到 `err.message`。
 * 2. 原生 Error：返回 `err.message`。
 * 3. 其他未知类型：返回 "未知错误"。
 *
 * @param err 任意被 catch 捕获的异常
 * @returns 可直接展示给用户的错误信息字符串
 */
export function extractError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return '未知错误';
}
