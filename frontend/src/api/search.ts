import { httpClient, tokenStorage } from './client';
import type {
  BatchSearchRequest,
  BatchSearchResponse,
  MultiDatasetSearchRequest,
  SearchByIdRequest,
  SearchByVectorRequest,
  SearchHit,
  SearchResponse,
} from '@/types/search';

// F6 SSE 流式检索：done 事件回填整体汇总信息
export interface StreamDoneEvent {
  type: 'done';
  dataset_id: number | null;
  top_k: number;
  latency_ms: number;
  total_candidates: number | null;
  index_backend: string | null;
  metric: string | null;
}

export interface StreamHitEvent extends SearchHit {
  type: 'hit';
}

export type SearchStreamEvent = StreamHitEvent | StreamDoneEvent;

// 解析 SSE 文本块：返回 {event, data} 数组，未识别到 event 字段时默认 message
const parseSseBlocks = (raw: string): Array<{ event: string; data: string }> => {
  const normalized = raw.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const blocks = normalized.split('\n\n').filter((b) => b.trim());
  return blocks.map((block) => {
    let event = 'message';
    const dataLines: string[] = [];
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue;
      if (line.startsWith('event:')) {
        event = line.slice('event:'.length).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice('data:'.length).replace(/^ /, ''));
      }
    }
    return { event, data: dataLines.join('\n') };
  });
};

/**
 * 以 SSE 流式方式调用 ``POST /search/by-vector-stream``。
 *
 * EventSource 仅支持 GET，无法承载请求体，因此这里直接使用 ``fetch`` +
 * ``ReadableStream`` 自行解析 SSE。``async generator`` 设计：每收到一条
 * ``event: hit`` yield 一个 ``StreamHitEvent``；最终 ``event: done`` yield
 * 一个 ``StreamDoneEvent`` 后生成器自然结束。
 *
 * @param payload by-vector 请求体（与 ``searchApi.byVector`` 同构）
 * @param init    透传 ``AbortSignal`` 以支持用户取消
 */
export async function* byVectorStream(
  payload: SearchByVectorRequest,
  init?: { signal?: AbortSignal },
): AsyncGenerator<SearchStreamEvent, void, void> {
  const token = tokenStorage.get();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch('/api/v1/search/by-vector-stream', {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
    signal: init?.signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    throw new Error(`SSE 流式检索失败：HTTP ${res.status} ${text}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // 找到所有完整 event（以空行结尾），剩余作为下次 buffer
      const lastSep = buffer.lastIndexOf('\n\n');
      if (lastSep === -1) continue;
      const ready = buffer.slice(0, lastSep + 2);
      buffer = buffer.slice(lastSep + 2);
      for (const { event, data } of parseSseBlocks(ready)) {
        if (!data) continue;
        if (event === 'hit') {
          const hit = JSON.parse(data) as SearchHit;
          yield { type: 'hit', ...hit };
        } else if (event === 'done') {
          const summary = JSON.parse(data) as Omit<StreamDoneEvent, 'type'>;
          yield { type: 'done', ...summary };
        }
      }
    }
    if (buffer.trim()) {
      for (const { event, data } of parseSseBlocks(buffer)) {
        if (!data) continue;
        if (event === 'hit') {
          const hit = JSON.parse(data) as SearchHit;
          yield { type: 'hit', ...hit };
        } else if (event === 'done') {
          const summary = JSON.parse(data) as Omit<StreamDoneEvent, 'type'>;
          yield { type: 'done', ...summary };
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// 相似检索 API
export const searchApi = {
  byId: async (payload: SearchByIdRequest): Promise<SearchResponse> => {
    const { data } = await httpClient.post<SearchResponse>('/search/by-id', payload);
    return data;
  },

  byVector: async (payload: SearchByVectorRequest): Promise<SearchResponse> => {
    const { data } = await httpClient.post<SearchResponse>('/search/by-vector', payload);
    return data;
  },

  byVectorStream,

  multiDataset: async (payload: MultiDatasetSearchRequest): Promise<SearchResponse> => {
    const { data } = await httpClient.post<SearchResponse>('/search/multi-dataset', payload);
    return data;
  },

  batch: async (payload: BatchSearchRequest): Promise<BatchSearchResponse> => {
    const { data } = await httpClient.post<BatchSearchResponse>('/search/batch', payload);
    return data;
  },
};
