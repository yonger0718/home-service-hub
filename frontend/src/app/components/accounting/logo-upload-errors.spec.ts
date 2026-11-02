import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';

import { MAX_LOGO_FILE_BYTES, logoFileTooLarge, logoUploadErrorMessage } from './logo-upload-errors';

const fail = (status: number, message: string) =>
  new HttpErrorResponse({ status, error: { code: status, message, trace_id: 't' } });

describe('logoUploadErrorMessage', () => {
  it('maps 413 to the size copy', () => {
    expect(logoUploadErrorMessage(fail(413, 'too big'))).toBe('圖片太大（上限 5 MB）');
  });
  it('maps 422 logo_too_large to the size copy', () => {
    expect(logoUploadErrorMessage(fail(422, 'logo_too_large'))).toBe('圖片太大（上限 5 MB）');
  });
  it('maps logo_format', () => {
    expect(logoUploadErrorMessage(fail(422, 'logo_format'))).toBe('只接受 PNG、JPEG、WebP');
  });
  it('maps logo_dimensions', () => {
    expect(logoUploadErrorMessage(fail(422, 'logo_dimensions'))).toBe('圖片尺寸不符（每邊 16–8192 px）');
  });
  it('maps logo_unreadable and logo_empty', () => {
    expect(logoUploadErrorMessage(fail(422, 'logo_unreadable'))).toBe('這個檔案無法當作圖片使用');
    expect(logoUploadErrorMessage(fail(422, 'logo_empty'))).toBe('這個檔案無法當作圖片使用');
  });
  it('maps 404 to unsupported', () => {
    expect(logoUploadErrorMessage(fail(404, 'not_found'))).toBe('伺服器尚未支援上傳');
  });
  it('falls back to the generic write message', () => {
    expect(logoUploadErrorMessage(new HttpErrorResponse({ status: 0 }))).toBe('無法連線到伺服器');
    expect(logoUploadErrorMessage(new Error('x'))).toBe('儲存失敗，請稍後再試');
  });
  it('flags files over 5 MB', () => {
    expect(logoFileTooLarge(new Blob([new Uint8Array(MAX_LOGO_FILE_BYTES + 1)]))).toBe(true);
    expect(logoFileTooLarge(new Blob([new Uint8Array(MAX_LOGO_FILE_BYTES)]))).toBe(false);
  });
});
