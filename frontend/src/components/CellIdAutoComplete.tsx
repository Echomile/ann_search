import { useCallback, useRef, useState } from 'react';
import { AutoComplete } from 'antd';
import { searchApi } from '@/api/search';

interface CellIdAutoCompleteProps {
  /** 候选所属数据集 ID；为空时不发起补全请求 */
  datasetId?: number;
  /** 受控值，由外层 Form.Item 注入 */
  value?: string;
  /** 受控变更回调，由外层 Form.Item 注入 */
  onChange?: (v: string) => void;
  placeholder?: string;
}

/**
 * cell_id 输入框的自动补全组件。
 *
 * 作为受控组件嵌入 Form.Item（自动注入 value/onChange）；输入时按 250ms 防抖调用
 * 后端 ``/search/cell-ids`` 拉取候选（前缀命中优先）。聚焦即拉默认候选；未选数据集
 * 时不发请求。可直接键入任意文本（不强制从候选中选择）。
 */
export const CellIdAutoComplete = ({
  datasetId,
  value,
  onChange,
  placeholder,
}: CellIdAutoCompleteProps) => {
  const [options, setOptions] = useState<{ value: string }[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchOptions = useCallback(
    (text: string) => {
      if (!datasetId) {
        setOptions([]);
        return;
      }
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(async () => {
        try {
          const ids = await searchApi.suggestCellIds(datasetId, text ?? '', 20);
          setOptions(ids.map((id) => ({ value: id })));
        } catch {
          setOptions([]);
        }
      }, 250);
    },
    [datasetId],
  );

  return (
    <AutoComplete
      value={value}
      onChange={(v) => onChange?.(v)}
      onSearch={fetchOptions}
      onFocus={() => fetchOptions(value ?? '')}
      options={options}
      placeholder={placeholder}
      filterOption={false}
      allowClear
    />
  );
};
