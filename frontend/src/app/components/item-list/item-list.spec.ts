import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { provideRouter } from '@angular/router';
import { describe, expect, it, beforeEach, vi } from 'vitest';

import { ItemListComponent } from './item-list';
import { ItemService } from '../../services/item.service';
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
});
