import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { of, throwError } from 'rxjs';
import { provideRouter } from '@angular/router';
import { describe, expect, it, beforeEach, vi } from 'vitest';

import { ItemListComponent } from './item-list';
import { ItemService } from '../../services/item.service';
import { ToastService } from '../../services/toast.service';
import { ItemResponse } from '../../models/item.model';

describe('ItemListComponent', () => {
  let component: ItemListComponent;
  let fixture: ComponentFixture<ItemListComponent>;
  let itemServiceMock: {
    getAllFiltered: ReturnType<typeof vi.fn>;
    getCategories: ReturnType<typeof vi.fn>;
    getLocations: ReturnType<typeof vi.fn>;
    create: ReturnType<typeof vi.fn>;
    update: ReturnType<typeof vi.fn>;
    delete: ReturnType<typeof vi.fn>;
    uploadImage: ReturnType<typeof vi.fn>;
    createTransaction: ReturnType<typeof vi.fn>;
    getTransactions: ReturnType<typeof vi.fn>;
  };

  const items: ItemResponse[] = [
    {
      id: 1,
      name: '洗碗精',
      category: '清潔',
      location: '廚房',
      quantity: 2,
      minQuantity: 2,
      targetQuantity: 6,
      stockStatus: 'LOW',
      createdAt: '',
      updatedAt: '',
    },
    {
      id: 2,
      name: '咖啡豆',
      category: '食品',
      location: '廚房',
      quantity: 5,
      minQuantity: 2,
      targetQuantity: 8,
      stockStatus: 'NORMAL',
      createdAt: '',
      updatedAt: '',
    },
  ];

  beforeEach(async () => {
    itemServiceMock = {
      getAllFiltered: vi.fn().mockReturnValue(of(items)),
      getCategories: vi.fn().mockReturnValue(of([])),
      getLocations: vi.fn().mockReturnValue(of([])),
      create: vi.fn().mockReturnValue(of({})),
      update: vi.fn().mockReturnValue(of({})),
      delete: vi.fn().mockReturnValue(of(undefined)),
      uploadImage: vi.fn().mockReturnValue(of({})),
      createTransaction: vi.fn().mockReturnValue(of({ item: null })),
      getTransactions: vi.fn().mockReturnValue(of([])),
    };

    await TestBed.configureTestingModule({
      imports: [ItemListComponent],
      providers: [
        provideRouter([]),
        { provide: ItemService, useValue: itemServiceMock },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ItemListComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('creates', () => {
    expect(component).toBeTruthy();
  });

  it('increments and decrements quantity with live status recomputation', () => {
    component.items.set([{ ...items[1], quantity: 3, minQuantity: 2 }]);
    fixture.detectChanges();

    component.adjustQuantity(component.items()[0], -1);
    fixture.detectChanges();

    expect(component.items()[0].quantity).toBe(2);
    expect(component.getStockStatusLabel(component.items()[0])).toBe('低庫存');
    expect(itemServiceMock.createTransaction).toHaveBeenCalledWith(2, expect.objectContaining({ type: 'CONSUME' }));

    component.adjustQuantity(component.items()[0], 1);

    expect(component.items()[0].quantity).toBe(3);
    expect(itemServiceMock.createTransaction).toHaveBeenCalledWith(2, expect.objectContaining({ type: 'RESTOCK' }));
  });

  it('does not decrement below zero or persist a no-op', () => {
    component.items.set([{ ...items[0], quantity: 0, stockStatus: 'OUT' }]);

    component.adjustQuantity(component.items()[0], -1);

    expect(component.items()[0].quantity).toBe(0);
    expect(itemServiceMock.createTransaction).not.toHaveBeenCalled();
  });

  it('filters to low-stock items only', () => {
    component.items.set(items);
    component.lowStockOnly = true;

    expect(component.displayedItems().map(item => item.name)).toEqual(['洗碗精']);
  });

  describe('feedback', () => {
    const serverError = () => throwError(() => new HttpErrorResponse({ status: 500 }));
    const toasts = () => TestBed.inject(ToastService);

    it('a failed list load shows 無法載入物品清單 with 重試, which loads again', () => {
      itemServiceMock.getAllFiltered.mockReturnValueOnce(throwError(() => new HttpErrorResponse({ status: 500 })));
      component.loadItems();
      expect(toasts().current()).toEqual(expect.objectContaining({ kind: 'error', text: '無法載入物品清單' }));
      expect(toasts().current()?.action?.label).toBe('重試');

      const calls = itemServiceMock.getAllFiltered.mock.calls.length;
      toasts().current()!.action!.run();
      expect(itemServiceMock.getAllFiltered).toHaveBeenCalledTimes(calls + 1);
      expect(component.items()).toEqual(items);
    });

    it('saveItem answered 500 shows 重試 that re-sends the captured payload once, also for an update; a 400 shows none', () => {
      itemServiceMock.create.mockReturnValueOnce(serverError());
      component.showDialog();
      component.newItem.name = '測試牙膏';
      component.saveItem();
      expect(toasts().current()).toEqual(expect.objectContaining({ kind: 'error', text: '建立失敗' }));
      const payload = itemServiceMock.create.mock.calls[0][0];
      component.displayDialog = false;
      component.newItem.name = '改過了';

      toasts().current()!.action!.run();
      expect(itemServiceMock.create).toHaveBeenCalledTimes(2);
      expect(itemServiceMock.create.mock.calls[1][0]).toEqual(payload);
      expect(toasts().current()?.text).toBe('物品已建立');

      itemServiceMock.update.mockReturnValueOnce(serverError()).mockReturnValueOnce(
        throwError(() => new HttpErrorResponse({ status: 400, error: { message: '名稱重複' } })));
      component.editItem(items[0]);
      component.saveItem();
      expect(toasts().current()).toEqual(expect.objectContaining({ kind: 'error', text: '更新失敗' }));
      toasts().current()!.action!.run();
      expect(itemServiceMock.update.mock.calls[1]).toEqual(itemServiceMock.update.mock.calls[0]);
      expect(toasts().current()).toEqual(expect.objectContaining({ kind: 'error', text: '名稱重複', action: null }));
    });

    it('deleteItem answered 500 shows 重試 that deletes the same item once', () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      itemServiceMock.delete.mockReturnValueOnce(serverError());
      component.deleteItem(2);
      expect(toasts().current()).toEqual(expect.objectContaining({ kind: 'error', text: '刪除失敗' }));

      toasts().current()!.action!.run();
      expect(itemServiceMock.delete.mock.calls).toEqual([[2], [2]]);
      expect(toasts().current()?.text).toBe('物品已刪除');
    });

    it('adjustQuantity answered 500 shows 重試 that sends the same adjustment once', () => {
      itemServiceMock.createTransaction.mockReturnValueOnce(serverError());
      const row = items[1];
      component.adjustQuantity(row, -1);
      expect(toasts().current()).toEqual(expect.objectContaining({ kind: 'error', text: '操作失敗' }));
      expect(toasts().current()?.action?.label).toBe('重試');

      toasts().current()!.action!.run();
      expect(itemServiceMock.createTransaction.mock.calls.map(call => [call[0], call[1].type])).toEqual([[2, 'CONSUME'], [2, 'CONSUME']]);
    });

    it('a 重試 of an image upload that succeeds after another item was opened does not write into that form', () => {
      itemServiceMock.uploadImage.mockReturnValueOnce(serverError());
      component.editItem(items[0]);
      component.onUpload({ files: [new File(['x'], '範例.png')] });
      expect(toasts().current()).toEqual(expect.objectContaining({ kind: 'error', text: '圖片上傳失敗' }));

      component.editItem(items[1]);
      const loads = itemServiceMock.getAllFiltered.mock.calls.length;
      itemServiceMock.uploadImage.mockReturnValueOnce(of({ ...items[0], imageUrl: '/img/dish.png' }));
      toasts().current()!.action!.run();

      expect(itemServiceMock.uploadImage.mock.calls.map(call => call[0])).toEqual([1, 1]);
      expect(component.newItem.id).toBe(2);
      expect(component.newItem.imageUrl).toBeUndefined();
      expect(itemServiceMock.getAllFiltered).toHaveBeenCalledTimes(loads + 1);
      expect(toasts().current()).toEqual(expect.objectContaining({ kind: 'done', text: '圖片上傳成功' }));
    });

    it('a 重試 of a save that succeeds after the form was reopened leaves the open form alone', () => {
      itemServiceMock.update.mockReturnValueOnce(serverError());
      component.editItem(items[0]);
      component.saveItem();
      component.editItem(items[1]);
      itemServiceMock.update.mockReturnValueOnce(of({}));
      toasts().current()!.action!.run();
      expect(component.displayDialog).toBe(true);
      expect(component.newItem.id).toBe(2);
      expect(toasts().current()?.text).toBe('物品已更新');
    });

    it('adjustQuantity 重試 re-sends the same id and delta once, moving the row as listed now (looked up by id)', () => {
      itemServiceMock.createTransaction.mockReturnValueOnce(serverError());
      component.items.set([{ ...items[1], quantity: 5 }]);
      component.adjustQuantity(component.items()[0], -1);
      // The failure reloads the list, which puts the server's quantity back (a different row object).
      const quantityOf = () => component.items().find(row => row.id === 2)?.quantity;
      expect(quantityOf()).toBe(5);
      itemServiceMock.createTransaction.mockClear();

      toasts().current()!.action!.run();
      expect(itemServiceMock.createTransaction).toHaveBeenCalledTimes(1);
      expect(itemServiceMock.createTransaction.mock.calls[0][0]).toBe(2);
      expect(itemServiceMock.createTransaction.mock.calls[0][1]).toEqual(expect.objectContaining({ type: 'CONSUME', deltaQuantity: 1 }));
      expect(quantityOf()).toBe(4);
    });

    it('adjustQuantity 重試 for a row that is gone sends the request and touches no row', () => {
      itemServiceMock.createTransaction.mockReturnValueOnce(serverError());
      component.adjustQuantity(items[1], -1);
      itemServiceMock.getAllFiltered.mockReturnValue(of([items[0]]));
      component.loadItems();
      itemServiceMock.createTransaction.mockClear();

      expect(() => toasts().current()!.action!.run()).not.toThrow();
      expect(itemServiceMock.createTransaction).toHaveBeenCalledTimes(1);
      expect(component.items().map(row => row.id)).toEqual([1]);
    });
  });
});
