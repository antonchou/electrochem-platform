import type { ApiClient, ExportFormat } from '../services/apiClient';

interface Props {
  api: ApiClient;
  experimentId: number;
  format: ExportFormat;
  className?: string;
  testId?: string;
  /** 下载失败的提示；缺省只打控制台日志 */
  onError?: (message: string) => void;
}

/**
 * 「导出 CSV/JSON」链接。点击时 fetch 成 blob 再下载：开发环境前后端跨源，
 * 直接用 <a download> 会整页跳走；href 保留真实地址，便于复制和 E2E 断言。
 */
export function ExportLink({ api, experimentId, format, className, testId, onError }: Props) {
  const url = api.exportUrl(experimentId, format);
  return (
    <a
      className={className}
      href={url}
      download
      data-testid={testId}
      onClick={(event) => {
        event.preventDefault();
        void api.downloadExport(url, `experiment_${experimentId}.${format}`).catch((err: unknown) => {
          if (onError) onError(err instanceof Error ? err.message : '导出失败');
          else console.error(err);
        });
      }}
    >
      导出 {format.toUpperCase()}
    </a>
  );
}
