// 通用格式化工具

/** 将文件大小（字节）格式化为人类可读字符串 */
export function formatBytes(bytes?: number | null, fractionDigits = 2): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return '-';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(fractionDigits)} ${units[i]}`;
}

/** 格式化时长（毫秒），自动换算为 ms / s / min */
export function formatDuration(ms?: number | null): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '-';
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(2)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes} min ${rest.toFixed(1)} s`;
}

/** 后端 build_time_seconds 单位为秒 */
export function formatSeconds(seconds?: number | null): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '-';
  return formatDuration(seconds * 1000);
}

/** 内存占用（MB）格式化 */
export function formatMemoryMb(mb?: number | null): string {
  if (mb === null || mb === undefined || Number.isNaN(mb)) return '-';
  if (mb < 1024) return `${mb.toFixed(2)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

/** 后端 ISO 时间字符串本地化展示 */
export function formatDateTime(value?: string | null): string {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

/** 数据集状态对应 Antd Tag 颜色 */
export function datasetStatusColor(status: string): string {
  switch (status) {
    case 'ready':
      return 'green';
    case 'uploading':
      return 'blue';
    case 'preprocessing':
      return 'gold';
    case 'failed':
      return 'red';
    default:
      return 'default';
  }
}

/** 索引状态对应 Antd Tag 颜色 */
export function indexStatusColor(status: string): string {
  switch (status) {
    case 'ready':
      return 'green';
    case 'building':
      return 'gold';
    case 'failed':
      return 'red';
    default:
      return 'default';
  }
}
