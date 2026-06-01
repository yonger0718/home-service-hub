import { Component, OnInit, inject, signal, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ItemService } from '../../services/item.service';
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
import { ToastModule } from 'primeng/toast';
import { TagModule } from 'primeng/tag';
import { AutoCompleteModule } from 'primeng/autocomplete';
import { FileUploadModule } from 'primeng/fileupload';
import { ImageModule } from 'primeng/image';
import { TooltipModule } from 'primeng/tooltip';
import { MessageService } from 'primeng/api';
import { BtnComponent } from '../ui/btn/btn';
import { TagComponent } from '../ui/tag/tag';

@Component({
  selector: 'app-item-list',
  imports: [
    CommonModule,
    FormsModule,
    TableModule,
    ButtonModule,
    DialogModule,
    InputTextModule,
    InputNumberModule,
    ProgressBarModule,
    TextareaModule,
    ToastModule,
    TagModule,
    AutoCompleteModule,
    FileUploadModule,
    ImageModule,
    TooltipModule,
    BtnComponent,
    TagComponent
  ],
  providers: [MessageService],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './item-list.html',
  styleUrl: './item-list.scss'
})
export class ItemListComponent implements OnInit {
  private itemService = inject(ItemService);
  private messageService = inject(MessageService);

  items = signal<ItemResponse[]>([]);
  history = signal<InventoryTransactionResponse[]>([]);
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

  loadItems(): void {
    this.itemService.getAllFiltered(this.searchKeyword, this.lowStockOnly).subscribe({
      next: data => this.items.set(data),
      error: () => this.messageService.add({ severity: 'error', summary: '錯誤', detail: '無法載入物品清單' })
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
    this.displayDialog = true;
  }

  editItem(item: ItemResponse) {
    this.newItem = { ...item };
    this.isEdit = true;
    this.displayDialog = true;
  }

  saveItem() {
    if (!this.canSaveItem()) {
      this.messageService.add({ severity: 'warn', summary: '提醒', detail: '請檢查名稱、數量與門檻欄位' });
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

    if (this.isEdit && this.newItem.id) {
      this.itemService.update(this.newItem.id, payload).subscribe({
        next: () => {
          this.messageService.add({ severity: 'success', summary: '成功', detail: '物品已更新' });
          this.displayDialog = false;
          this.loadItems();
        },
        error: err => this.messageService.add({ severity: 'error', summary: '錯誤', detail: err?.error?.message || '更新失敗' })
      });
      return;
    }

    this.itemService.create(payload).subscribe({
      next: () => {
        this.messageService.add({ severity: 'success', summary: '成功', detail: '物品已建立' });
        this.displayDialog = false;
        this.loadItems();
      },
      error: err => this.messageService.add({ severity: 'error', summary: '錯誤', detail: err?.error?.message || '建立失敗' })
    });
  }

  onUpload(event: { files: File[] }) {
    const file = event.files[0];
    if (this.isEdit && this.newItem.id) {
      this.itemService.uploadImage(this.newItem.id, file).subscribe({
        next: updatedItem => {
          this.newItem.imageUrl = updatedItem.imageUrl;
          this.messageService.add({ severity: 'success', summary: '成功', detail: '圖片上傳成功' });
          this.loadItems();
        },
        error: () => this.messageService.add({ severity: 'error', summary: '錯誤', detail: '圖片上傳失敗' })
      });
    }
  }

  consumeOne(item: ItemResponse) {
    this.adjustQuantity(item, -1);
  }

  adjustQuantity(item: ItemResponse, delta: 1 | -1): void {
    if (delta < 0 && item.quantity <= 0) {
      return;
    }

    const nextQuantity = Math.max(0, item.quantity + delta);
    this.items.update(rows => rows.map(row =>
      row.id === item.id ? this.withQuantity(row, nextQuantity) : row,
    ));

    const payload: InventoryTransactionRequest = delta > 0
      ? { type: 'RESTOCK', deltaQuantity: 1, operatorName: 'web-ui', reason: '快速補貨 +1' }
      : { type: 'CONSUME', deltaQuantity: 1, operatorName: 'web-ui', reason: '快速使用 -1' };

    this.itemService.createTransaction(item.id, payload).subscribe({
      next: result => {
        if (result?.item) {
          this.items.update(rows => rows.map(row => row.id === item.id ? result.item : row));
        }
      },
      error: err => {
        this.items.update(rows => rows.map(row => row.id === item.id ? item : row));
        this.messageService.add({ severity: 'error', summary: '錯誤', detail: err?.error?.message || '操作失敗' });
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
    this.submitTransaction(this.selectedItem.id, payload, successMessage, () => {
      this.displayActionDialog = false;
    });
  }

  openHistory(item: ItemResponse) {
    this.itemService.getTransactions(item.id, 50).subscribe({
      next: rows => {
        this.history.set(rows);
        this.displayHistoryDialog = true;
      },
      error: () => this.messageService.add({ severity: 'error', summary: '錯誤', detail: '無法載入異動歷史' })
    });
  }

  deleteItem(id: number): void {
    if (confirm('確定要刪除此物品嗎？')) {
      this.itemService.delete(id).subscribe({
        next: () => {
          this.messageService.add({ severity: 'success', summary: '成功', detail: '物品已刪除' });
          this.loadItems();
        },
        error: () => this.messageService.add({ severity: 'error', summary: '錯誤', detail: '刪除失敗' })
      });
    }
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
    this.itemService.createTransaction(itemId, payload).subscribe({
      next: () => {
        this.messageService.add({ severity: 'success', summary: '成功', detail: successMessage });
        this.loadItems();
        callback?.();
      },
      error: err => this.messageService.add({ severity: 'error', summary: '錯誤', detail: err?.error?.message || '操作失敗' })
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
