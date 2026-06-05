import { Pipe, PipeTransform } from '@angular/core';

@Pipe({
  name: 'nativeAmount',
  standalone: true,
  pure: true,
})
export class NativeAmountPipe implements PipeTransform {
  transform(value: number | string | null | undefined, currency: string | null | undefined): string {
    if (value === null || value === undefined || value === '') return '—';

    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return '—';

    const decimals = currency === 'GBp' ? 4 : 2;
    const amount = numeric.toFixed(decimals);
    return currency && currency !== 'TWD' ? `${amount} ${currency}` : amount;
  }
}
