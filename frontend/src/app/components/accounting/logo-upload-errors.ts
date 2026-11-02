import { HttpErrorResponse } from '@angular/common/http';

import { writeErrorMessage } from './http-errors';

export const MAX_LOGO_FILE_BYTES = 5 * 1024 * 1024;
export const LOGO_ACCEPT = 'image/png,image/jpeg,image/webp';

const TOO_LARGE = '圖片太大（上限 5 MB）';

export function logoFileTooLarge(file: Blob): boolean {
  return file.size > MAX_LOGO_FILE_BYTES;
}

/** One line for a failed logo upload; the 422 `message` is exactly the backend token. */
export function logoUploadErrorMessage(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    if (err.status === 413) {
      return TOO_LARGE;
    }
    const body = err.error as { message?: unknown } | null;
    const token = typeof body?.message === 'string' ? body.message.split(':', 1)[0].trim() : null;
    if (err.status === 422) {
      switch (token) {
        case 'logo_too_large':
          return TOO_LARGE;
        case 'logo_format':
          return '只接受 PNG、JPEG、WebP';
        case 'logo_dimensions':
          return '圖片尺寸不符（每邊 16–8192 px）';
        case 'logo_unreadable':
        case 'logo_empty':
          return '這個檔案無法當作圖片使用';
      }
    }
    if (err.status === 404) {
      return '伺服器尚未支援上傳';
    }
  }
  return writeErrorMessage(err);
}
