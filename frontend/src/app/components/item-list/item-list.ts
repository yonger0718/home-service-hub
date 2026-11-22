import { HttpErrorResponse } from '@angular/common/http';
import { finalize } from 'rxjs';
import { Component, OnInit, inject, signal, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ItemService } from '../../services/item.service';
import { injectToasts } from '../../services/toast.service';
import { guardedRetry } from '../../services/toast-retry';
import {
  InventoryTransactionRequest,
  InventoryTransactionResponse,
  ItemRequest,
  ItemResponse
} from '../../models/item.model';
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { InputNumberModule } from 'primeng/inputnumber';
import { ProgressBarModule } from 'primeng/progressbar';
import { TextareaModule } from 'primeng/textarea';
import { TagModule } from 'primeng/tag';
import { AutoCompleteModule } from 'primeng/autocomplete';
import { FileUploadModule } from 'primeng/fileupload';
import { ImageModule } from 'primeng/image';
import { TooltipModule } from 'primeng/tooltip';
import { BtnComponent } from '../ui/btn/btn';
import { TagComponent } from '../ui/tag/tag';
import { PageHeaderComponent } from '../shell/page-header/page-header';

@Component({
  selector: 'app-item-list',
  imports: [
    PageHeaderComponent,
    CommonModule,
    FormsModule,
    TableModule,
    ButtonModule,
    DialogModule,
    InputTextModule,
    InputNumberModule,
    ProgressBarModule,
    TextareaModule,
    TagModule,
    AutoCompleteModule,
    FileUploadModule,
    ImageModule,
    TooltipModule,
    BtnComponent,
    TagComponent
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './item-list.html',
  styleUrl: './item-list.scss'
})
export class ItemListComponent implements OnInit {
  private itemService = inject(ItemService);
  private readonly toasts = injectToasts();

  items = signal<ItemResponse[]>([]);
  history = signal<InventoryTransactionResponse[]>([]);
  /** Bumped each time the item form or the quick-action dialog opens: a late answer (or 重試) touches a dialog only if it is still the one it was sent from. */
  private dialogSession = 0;
  private savingItem = false;
  private uploading = false;
  private deleting = false;
  private actionBusy = false;
  private readonly adjusting = new Set<number>();
  displayDialog = false;
  displayActionDialog = false;
  displayHistoryDialog = false;
  isEdit = false;
  lowStockOnly = false;

  selectedItem: ItemResponse | null = null;
  actionType: 'RESTOCK' | 'ADJUST' = 'RESTOCK';
  quickActionTitle = '';
  actionAmount = 1;
  actualAmount = 0;
  actionReason = '';

  newItem: ItemRequest & Partial<ItemResponse> = this.resetNewItem();
  searchKeyword = '';
  allCategories: string[] = [];
  filteredCategories: string[] = [];
  allLocations: string[] = [];
  filteredLocations: string[] = [];

  displayedItems(): ItemResponse[] {
    return this.lowStockOnly
      ? this.items().filter(item => this.isLowStock(item))
      : this.items();
  }

  calculateStockPercentage(item: ItemResponse): number {
    if (!item.targetQuantity || item.targetQuantity <= 0) {
      return item.quantity > 0 ? 100 : 0;
    }
    return Math.min(100, (item.quantity / item.targetQuantity) * 100);
  }

  ngOnInit(): void {
    this.loadItems();
    this.loadMetadata();
  }

  loadMetadata(): void {
    this.itemService.getCategories().subscribe(cats => this.allCategories = cats);
    this.itemService.getLocations().subscribe(locs => this.allLocations = locs);
  }

  /** `silent`: a failure raises no toast (a reload started from a save's error path must not replace that toast). */
  loadItems(options: { readonly silent?: boolean } = {}): void {
    this.itemService.getAllFiltered(this.searchKeyword, this.lowStockOnly).subscribe({
      next: data => this.items.set(data),
      error: () => {
        if (!options.silent) this.toasts.error('無法載入物品清單', { retry: () => this.loadItems() });
      }
    });
  }

  filterCategories(event: { query: string }) {
    const query = event.query.toLowerCase();
    this.filteredCategories = this.allCategories.filter(c => c.toLowerCase().includes(query));
  }

  filterLocations(event: { query: string }) {
    const query = event.query.toLowerCase();
    this.filteredLocations = this.allLocations.filter(l => l.toLowerCase().includes(query));
  }

  resetNewItem(): ItemRequest & Partial<ItemResponse> {
    return {
      name: '',
      category: '',
      location: '',
      quantity: 1,
      minQuantity: null,
      targetQuantity: null,
      isConsumable: true,
      status: 'ACTIVE',
      note: ''
    };
  }

  showDialog() {
    this.newItem = this.resetNewItem();
    this.isEdit = false;
    this.dialogSession++;
    this.displayDialog = true;
  }

  editItem(item: ItemResponse) {
    this.newItem = { ...item };
    this.isEdit = true;
    this.dialogSession++;
    this.displayDialog = true;
  }

  saveItem() {
    if (!this.canSaveItem()) {
      this.toasts.error('請檢查名稱、數量與門檻欄位');
      return;
    }

    const payload: ItemRequest = {
      name: this.newItem.name,
      category: this.newItem.category || '',
      location: this.newItem.location || '',
      quantity: this.newItem.quantity,
      minQuantity: this.newItem.minQuantity ?? null,
      targetQuantity: this.newItem.targetQuantity ?? null,
      isConsumable: this.newItem.isConsumable ?? true,
      status: this.newItem.status || 'ACTIVE',
      note: this.newItem.note || '',
      imageUrl: this.newItem.imageUrl || undefined
    };

    this.sendItem(payload, this.isEdit && this.newItem.id ? this.newItem.id : null, this.dialogSession);
  }

  private sendItem(payload: ItemRequest, id: number | null, session: number): void {
    this.savingItem = true;
    const request = id !== null ? this.itemService.update(id, payload) : this.itemService.create(payload);
    request.pipe(finalize(() => (this.savingItem = false))).subscribe({
      next: () => {
        this.toasts.done(id !== null ? '物品已更新' : '物品已建立');
        if (session === this.dialogSession) this.displayDialog = false;
        this.loadItems();
      },
      error: err => {
        const text = err?.error?.message || (id !== null ? '更新失敗' : '建立失敗');
        this.toasts.error(text, {
          retry: guardedRetry(err, this.toasts, text, () => this.savingItem, () => this.sendItem(payload, id, session)),
        });
      }
    });
  }

  onUpload(event: { files: File[] }) {
    const file = event.files[0];
    if (this.isEdit && this.newItem.id) {
      this.sendUpload(this.newItem.id, file, this.dialogSession);
    }
  }

  private sendUpload(id: number, file: File, session: number): void {
    this.uploading = true;
    this.itemService.uploadImage(id, file).pipe(finalize(() => (this.uploading = false))).subscribe({
      next: updatedItem => {
        // Only the form the file was chosen in gets the new image; one opened since (another item) is left alone.
        if (session === this.dialogSession && this.newItem.id === id) this.newItem.imageUrl = updatedItem.imageUrl;
        this.toasts.done('圖片上傳成功');
        this.loadItems();
      },
      error: err => {
        const text = '圖片上傳失敗';
        this.toasts.error(text, {
          retry: guardedRetry(err, this.toasts, text, () => this.uploading, () => this.sendUpload(id, file, session)),
        });
      }
    });
  }

  consumeOne(item: ItemResponse) {
    this.adjustQuantity(item, -1);
  }

  adjustQuantity(item: ItemResponse, delta: 1 | -1): void {
    if (delta < 0 && item.quantity <= 0) {
      return;
    }
    this.sendAdjust(item.id, delta);
  }

  /**
   * The row moves at once (looked up by id; nothing happens to the list if it is gone). On failure the list is
   * loaded again, which puts the server's quantity back; 重試 sends the same id and delta again and moves the
   * row (as it is listed now) by the delta once more.
   */
  private sendAdjust(itemId: number, delta: 1 | -1): void {
    this.items.update(rows => rows.map(row =>
      row.id === itemId ? this.withQuantity(row, Math.max(0, row.quantity + delta)) : row,
    ));

    const payload: InventoryTransactionRequest = delta > 0
      ? { type: 'RESTOCK', deltaQuantity: 1, operatorName: 'web-ui', reason: '快速補貨 +1' }
      : { type: 'CONSUME', deltaQuantity: 1, operatorName: 'web-ui', reason: '快速使用 -1' };

    this.adjusting.add(itemId);
    this.itemService.createTransaction(itemId, payload).pipe(finalize(() => this.adjusting.delete(itemId))).subscribe({
      next: result => {
        if (result?.item) {
          this.items.update(rows => rows.map(row => row.id === itemId ? result.item : row));
        }
      },
      error: err => {
        this.loadItems({ silent: true });
        const text = err?.error?.message || '操作失敗';
        this.toasts.error(text, {
          retry: guardedRetry(err, this.toasts, text, () => this.adjusting.has(itemId), () => this.sendAdjust(itemId, delta)),
        });
      },
    });
  }

  openQuickAction(item: ItemResponse, type: 'RESTOCK' | 'ADJUST') {
    this.selectedItem = item;
    this.actionType = type;
    this.quickActionTitle = type === 'RESTOCK' ? '快速補貨' : '盤點修正';
    this.actionAmount = 1;
    this.actualAmount = item.quantity;
    this.actionReason = '';
    this.dialogSession++;
    this.displayActionDialog = true;
  }

  submitAction() {
    if (!this.selectedItem) {
      return;
    }

    const payload: InventoryTransactionRequest = {
      type: this.actionType,
      operatorName: 'web-ui',
      reason: this.actionReason || undefined
    };

    if (this.actionType === 'RESTOCK') {
      payload.deltaQuantity = this.actionAmount;
    } else {
      payload.actualQuantity = this.actualAmount;
    }

    const successMessage = this.actionType === 'RESTOCK' ? '補貨成功' : '盤點修正成功';
    const session = this.dialogSession;
    this.submitTransaction(this.selectedItem.id, payload, successMessage, () => {
      if (session === this.dialogSession) this.displayActionDialog = false;
    });
  }

  openHistory(item: ItemResponse) {
    this.itemService.getTransactions(item.id, 50).subscribe({
      next: rows => {
        this.history.set(rows);
        this.displayHistoryDialog = true;
      },
      error: () => this.toasts.error('無法載入異動歷史')
    });
  }

  deleteItem(id: number): void {
    if (confirm('確定要刪除此物品嗎？')) {
      this.sendDelete(id);
    }
  }

  private sendDelete(id: number): void {
    this.deleting = true;
    this.itemService.delete(id).pipe(finalize(() => (this.deleting = false))).subscribe({
      next: () => this.deleted(),
      error: err => {
        // A 404 means the item is already gone (an earlier attempt committed, or it was deleted elsewhere): done.
        if (err instanceof HttpErrorResponse && err.status === 404) {
          this.deleted();
          return;
        }
        const text = '刪除失敗';
        this.toasts.error(text, {
          retry: guardedRetry(err, this.toasts, text, () => this.deleting, () => this.sendDelete(id)),
        });
      }
    });
  }

  private deleted(): void {
    this.toasts.done('物品已刪除');
    this.loadItems();
  }

  getStockStatusLabel(item: ItemResponse): string {
    const status = this.liveStockStatus(item);
    if (status === 'OUT') return '缺貨';
    if (status === 'LOW') return '低庫存';
    return '正常';
  }

  getStockStatusSeverity(item: ItemResponse): 'danger' | 'warn' | 'success' {
    const status = this.liveStockStatus(item);
    if (status === 'OUT') return 'danger';
    if (status === 'LOW') return 'warn';
    return 'success';
  }

  isLowStock(item: ItemResponse): boolean {
    return this.liveStockStatus(item) !== 'NORMAL';
  }

  canSaveItem(): boolean {
    const qty = this.newItem.quantity;
    const min = this.newItem.minQuantity;
    const target = this.newItem.targetQuantity;
    if (!this.newItem.name || qty == null || qty < 0) {
      return false;
    }
    if (min != null && min < 0) {
      return false;
    }
    if (target != null && target < 0) {
      return false;
    }
    return true;
  }

  private submitTransaction(
    itemId: number,
    payload: InventoryTransactionRequest,
    successMessage: string,
    callback?: () => void
  ) {
    this.actionBusy = true;
    this.itemService.createTransaction(itemId, payload).pipe(finalize(() => (this.actionBusy = false))).subscribe({
      next: () => {
        this.toasts.done(successMessage);
        this.loadItems();
        callback?.();
      },
      error: err => {
        const text = err?.error?.message || '操作失敗';
        this.toasts.error(text, {
          retry: guardedRetry(err, this.toasts, text, () => this.actionBusy, () => this.submitTransaction(itemId, payload, successMessage, callback)),
        });
      }
    });
  }

  private withQuantity(item: ItemResponse, quantity: number): ItemResponse {
    return {
      ...item,
      quantity,
      isLowStock: quantity <= (item.minQuantity ?? 0),
      stockStatus: quantity === 0 ? 'OUT' : quantity <= (item.minQuantity ?? 0) ? 'LOW' : 'NORMAL',
    };
  }

  private liveStockStatus(item: ItemResponse): 'OUT' | 'LOW' | 'NORMAL' {
    if (item.quantity <= 0) return 'OUT';
    if (item.minQuantity != null && item.quantity <= item.minQuantity) return 'LOW';
    if (item.stockStatus === 'OUT' || item.stockStatus === 'LOW') return item.stockStatus;
    return 'NORMAL';
  }
}
