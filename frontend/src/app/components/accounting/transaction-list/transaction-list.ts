import { Component, OnInit, inject, signal, ViewChild, computed, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AccountingService } from '../../../services/accounting.service';
import { Transaction, Category, CreditCard, PaymentMethod } from '../../../models/accounting.model';
import { forkJoin } from 'rxjs';
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { InputNumberModule } from 'primeng/inputnumber';
import { DialogModule } from 'primeng/dialog';
import { DatePickerModule } from 'primeng/datepicker';
import { SelectModule } from 'primeng/select';
import { TagModule } from 'primeng/tag';
import { ToastModule } from 'primeng/toast';
import { TooltipModule } from 'primeng/tooltip';
import { MenuModule } from 'primeng/menu';
import { RadioButtonModule } from 'primeng/radiobutton';
import { SelectButtonModule } from 'primeng/selectbutton';
import { MessageService, MenuItem } from 'primeng/api';
import { Menu } from 'primeng/menu';
import { BtnComponent } from '../../ui/btn/btn';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import { TimelineComponent, TimelineRow } from '../../ui/timeline/timeline';

@Component({
  selector: 'app-transaction-list',
  imports: [
    CommonModule, 
    FormsModule, 
    TableModule, 
    ButtonModule, 
    InputTextModule, 
    InputNumberModule,
    DialogModule, 
    SelectModule,
    TagModule,
    DatePickerModule,
    ToastModule,
    TooltipModule,
    MenuModule,
    RadioButtonModule,
    SelectButtonModule,
    BtnComponent,
    SegToggleComponent,
    TimelineComponent
  ],
  providers: [MessageService],
  templateUrl: './transaction-list.html',
  styleUrl: './transaction-list.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class TransactionListComponent implements OnInit {
  private accountingService = inject(AccountingService);
  private messageService = inject(MessageService);
  private currentRequestId = 0;

  @ViewChild('menu') menu!: Menu;
  menuItems: MenuItem[] = [];

  transactions = signal<Transaction[]>([]);
  categories = signal<Category[]>([]);
  cards = signal<CreditCard[]>([]);
  paymentMethods = signal<PaymentMethod[]>([]);
  
  // Filter Signals
  selectedCategory = signal<string | undefined>(undefined);
  selectedPaymentMethod = signal<string | undefined>(undefined);
  selectedKeyword = signal<string>('');
  selectedType = signal<'ALL' | 'EXPENSE' | 'INCOME' | 'CARD'>('ALL');
  selectedMonth = signal<Date>(new Date());

  monthTransactions = computed(() => {
    const current = this.selectedMonth();
    const year = current.getFullYear();
    const month = current.getMonth();
    return this.transactions().filter(txn => {
      const txnDate = new Date(txn.date);
      return txnDate.getFullYear() === year && txnDate.getMonth() === month;
    });
  });

  monthSummary = computed(() => {
    const txns = this.monthTransactions();
    const refundAmount = txns
      .filter(t => t.transactionType === 'INCOME' && t.relatedTransactionId)
      .reduce((sum, t) => sum + (t.paidAmount || 0), 0);

    const rawExpense = txns
      .filter(t => t.transactionType === 'EXPENSE')
      .reduce((sum, t) => sum + (t.paidAmount || 0), 0);
    const expense = Math.max(rawExpense - refundAmount, 0);

    const income = txns
      .filter(t => t.transactionType === 'INCOME' && !t.relatedTransactionId)
      .reduce((sum, t) => sum + (t.paidAmount || 0), 0);

    return {
      expense,
      income,
      net: income - expense,
      count: txns.length
    };
  });

  activeFilterTags = computed(() => {
    const tags: string[] = [];
    const cat = this.selectedCategory();
    const pm = this.selectedPaymentMethod();
    const keyword = this.selectedKeyword().trim();
    const type = this.selectedType();

    if (cat) tags.push(`分類：${cat}`);
    if (pm) tags.push(`支付：${pm}`);
    if (type !== 'ALL') tags.push(`類型：${type === 'EXPENSE' ? '支出' : type === 'INCOME' ? '收入' : '信用卡'}`);
    if (keyword) tags.push(`關鍵字：${keyword}`);
    return tags;
  });

  hasActiveFilters = computed(() => this.activeFilterTags().length > 0);

  filteredTransactions = computed(() => {
    let result = this.monthTransactions();
    const cat = this.selectedCategory();
    const pmValue = this.selectedPaymentMethod();
    const keyword = this.selectedKeyword().trim().toLowerCase();
    const type = this.selectedType();

    if (cat) {
      result = result.filter(txn => txn.categoryName === cat);
    }
    
    if (pmValue) {
        // 篩選時同時比對 paymentMethod 文字或 cardId (若選的是卡片)
        const isCardId = !isNaN(Number(pmValue));
        if (isCardId) {
            const cardId = Number(pmValue);
            result = result.filter(txn => txn.cardId === cardId);
        } else {
            result = result.filter(txn => txn.paymentMethod === pmValue);
        }
    }

    if (type === 'CARD') {
      result = result.filter(txn => !!txn.cardId);
    } else if (type !== 'ALL') {
      result = result.filter(txn => txn.transactionType === type);
    }

    if (keyword) {
      result = result.filter(txn =>
        (txn.item || '').toLowerCase().includes(keyword) ||
        (txn.note || '').toLowerCase().includes(keyword)
      );
    }

    return result;
  });

  displayDialog = false;
  isEdit = false;
  isGeneratingRecurring = false;
  paidAmountOverridden = false;
  txnDate = new Date();
  newTxn: any = this.resetNewTxn();
  selectedPaymentValue: string | null = null;

  typeOptions = [
    { label: '支出', value: 'EXPENSE' },
    { label: '收入', value: 'INCOME' },
    { label: '信用卡', value: 'CARD' }
  ];

  readonly filterTypeOptions: SegToggleOption[] = [
    { label: '全部', value: 'ALL' },
    { label: '支出', value: 'EXPENSE' },
    { label: '收入', value: 'INCOME' },
    { label: '信用卡', value: 'CARD' },
  ];

  readonly dialogTypeOptions: SegToggleOption[] = [
    { label: '支出', value: 'EXPENSE' },
    { label: '收入', value: 'INCOME' },
    { label: '信用卡', value: 'CARD' },
  ];

  readonly timelineRows = computed<TimelineRow[]>(() =>
    this.filteredTransactions().map(txn => ({
      date: txn.date,
      side: txn.transactionType === 'INCOME' ? 'buy' : 'sell',
      sideLabel: txn.transactionType === 'INCOME' ? '收' : '支',
      primary: txn.item,
      meta: [txn.categoryName, txn.cardName || txn.paymentMethod, txn.note].filter(Boolean).join(' · '),
      amount: `${txn.transactionType === 'EXPENSE' ? '-' : '+'}${this.formatCurrency(txn.transactionAmount)}`,
      amountVariant: txn.transactionType === 'INCOME' ? 'income' : 'expense',
    })),
  );

  paymentOptions = signal<any[]>([]);
  filterPaymentOptions = signal<any[]>([]);

  toolOptions = computed(() => 
    this.paymentMethods().map(m => ({ label: m.name, value: m.name }))
  );

  ngOnInit() {
    this.loadLookupData();
    this.loadData();
  }

  getSelectedMonthLabel() {
    const month = this.selectedMonth();
    return `${month.getFullYear()}年${month.getMonth() + 1}月`;
  }

  goPrevMonth() {
    const current = this.selectedMonth();
    const nextDate = new Date(current.getFullYear(), current.getMonth() - 1, 1);
    this.selectedMonth.set(nextDate);
    this.loadTransactions(nextDate);
  }

  goNextMonth() {
    const current = this.selectedMonth();
    const nextDate = new Date(current.getFullYear(), current.getMonth() + 1, 1);
    this.selectedMonth.set(nextDate);
    this.loadTransactions(nextDate);
  }

  goCurrentMonth() {
    const now = new Date();
    const nextDate = new Date(now.getFullYear(), now.getMonth(), 1);
    this.selectedMonth.set(nextDate);
    this.loadTransactions(nextDate);
  }

  clearFilters() {
    this.selectedCategory.set(undefined);
    this.selectedPaymentMethod.set(undefined);
    this.selectedType.set('ALL');
    this.selectedKeyword.set('');
  }

  setFilterType(value: string) {
    this.selectedType.set(value as 'ALL' | 'EXPENSE' | 'INCOME' | 'CARD');
  }

  isNewDay(index: number): boolean {
    if (index === 0) return true;
    const current = this.filteredTransactions()[index];
    const previous = this.filteredTransactions()[index - 1];
    return current.date !== previous.date;
  }

  loadData() {
    this.loadTransactions(this.selectedMonth());
  }

  loadTransactions(currentMonth: Date) {
    const reqId = ++this.currentRequestId;
    const year = currentMonth.getFullYear();
    const month = currentMonth.getMonth(); // 0-indexed
    
    // Construct local date range strings safely
    const dateFrom = `${year}-${(month + 1).toString().padStart(2, '0')}-01`;
    const lastDay = new Date(year, month + 1, 0).getDate();
    const dateTo = `${year}-${(month + 1).toString().padStart(2, '0')}-${lastDay.toString().padStart(2, '0')}`;

    this.accountingService.getTransactions(0, 1000, undefined, dateFrom, dateTo).subscribe({
      next: (data) => {
        if (reqId === this.currentRequestId) {
          const sorted = data.sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());
          this.transactions.set(sorted);

          // Truncation detection: if count is exactly 1000, warn the user
          if (data.length === 1000) {
            this.messageService.add({
              severity: 'warn',
              summary: '資料可能被截斷',
              detail: '本月交易量已達上限 (1000 筆)，部分較早的交易可能未顯示。',
              life: 8000
            });
          }
        }
      },
      error: (err) => {
        console.error('Failed to load transactions:', err);
        if (reqId === this.currentRequestId) {
          this.messageService.add({
            severity: 'error',
            summary: '載入失敗',
            detail: '無法取得本月交易明細，請稍後再試。'
          });
        }
      }
    });
  }

  loadLookupData() {
    this.accountingService.getCategories().subscribe({
      next: data => this.categories.set(data),
      error: err => console.error('Failed to load categories', err)
    });
    
    forkJoin({
        cards: this.accountingService.getCards(),
        methods: this.accountingService.getPaymentMethods()
    }).subscribe({
      next: ({ cards, methods }) => {
        this.cards.set(cards);
        this.paymentMethods.set(methods);
        
        // 1. 建立「整合型支付選單」: 現金 + 所有的信用卡
        const combined = [
            { label: '現金', value: 'CASH', type: 'CASH' },
            ...cards.map(c => ({ 
                label: c.name, 
                value: `CARD_${c.id}`, 
                type: 'CARD',
                cardId: c.id,
                defaultTool: c.defaultPaymentMethod || 'Apple Pay'
            }))
        ];
        this.paymentOptions.set(combined);

        // 2. 篩選器選項：保持原樣
        const filterOptions = [
            { label: '現金', value: '現金' },
            { label: '--- 信用卡 ---', value: null, disabled: true },
            ...cards.map(c => ({ label: c.name, value: c.id.toString() }))
        ];
        this.filterPaymentOptions.set(filterOptions);
      },
      error: err => console.error('Failed to load payment options metadata', err)
    });
  }

  resetNewTxn() {
    this.selectedPaymentValue = 'CASH';
    this.paidAmountOverridden = false;
    return {
      item: '',
      date: '',
      categoryId: null,
      paidAmount: 0,
      transactionAmount: 0,
      paymentMethod: '現金',
      cardId: null,
      transactionType: 'EXPENSE',
      note: ''
    };
  }

  showDialog() {
    this.newTxn = this.resetNewTxn();
    this.selectedPaymentValue = 'CASH';
    this.paidAmountOverridden = false;
    this.txnDate = new Date();
    this.isEdit = false;
    this.displayDialog = true;
  }

  setDialogType(value: string) {
    this.newTxn.transactionType = value === 'CARD' ? 'CARD' : value;
    if (value === 'CARD' && this.cards().length > 0) {
      const card = this.cards()[0];
      this.selectedPaymentValue = `CARD_${card.id}`;
      this.newTxn.cardId = card.id;
      this.newTxn.paymentMethod = card.defaultPaymentMethod || '信用卡';
    } else {
      this.newTxn.cardId = null;
      this.selectedPaymentValue = 'CASH';
      this.newTxn.paymentMethod = '現金';
    }
  }

  showMenu(event: MouseEvent, txn: Transaction) {
      this.menuItems = [
          { 
              label: '編輯', 
              icon: 'pi pi-pencil', 
              command: () => this.editTransaction(txn) 
          },
          { 
              label: '申請退款/沖銷', 
              icon: 'pi pi-undo', 
              visible: txn.transactionType === 'EXPENSE' && this.getMaxRefundableAmount(txn) > 0,
              command: () => this.onRefund(txn)
          },
          { separator: true },
          { 
              label: '刪除', 
              icon: 'pi pi-trash', 
              styleClass: 'text-danger', 
              command: () => this.deleteTransaction(txn.id) 
          }
      ];
      this.menu.toggle(event);
  }

  editTransaction(txn: Transaction) {
      this.isEdit = true;
      this.newTxn = {
        id: txn.id,
        item: txn.item,
        date: txn.date,
        categoryId: txn.categoryId,
        paidAmount: txn.paidAmount,
        transactionAmount: txn.transactionAmount,
        paymentMethod: txn.paymentMethod,
        cardId: txn.cardId ?? null,
        transactionType: txn.transactionType,
        note: txn.note ?? ''
      };
      this.paidAmountOverridden = txn.transactionAmount !== txn.paidAmount;
      this.txnDate = new Date(txn.date);
      this.selectedPaymentValue = txn.cardId ? `CARD_${txn.cardId}` : 'CASH';
      this.displayDialog = true;
  }

  onTransactionAmountChange(value: number | null | undefined) {
      const amount = value ?? 0;
      this.newTxn.transactionAmount = amount;
      if (!this.paidAmountOverridden) {
          this.newTxn.paidAmount = amount;
      }
  }

  onPaidAmountChange(value: number | null | undefined) {
      this.newTxn.paidAmount = value ?? 0;
      this.paidAmountOverridden = true;
  }

  onCategoryChange(id: number) {
      this.newTxn.categoryId = id;
  }

  onCombinedPaymentChange(event: any) {
      const selected = this.paymentOptions().find(o => o.value === event.value);
      if (!selected) return;

      if (selected.type === 'CASH') {
          this.newTxn.paymentMethod = '現金';
          this.newTxn.cardId = null;
      } else if (selected.type === 'CARD') {
          this.newTxn.cardId = selected.cardId;
          this.newTxn.paymentMethod = selected.defaultTool;
      }
      
      // 若使用者尚未手動覆寫，維持交易金額與實付金額同步
      if (!this.paidAmountOverridden) {
          this.newTxn.paidAmount = this.newTxn.transactionAmount;
      }
  }

  onRefund(txn: Transaction) {
      const maxRefundableAmount = this.getMaxRefundableAmount(txn);
      if (maxRefundableAmount <= 0) {
        this.messageService.add({ severity: 'warn', summary: '無可退款金額', detail: '這筆交易目前沒有可建立的退款/沖銷額度。' });
        return;
      }

      const amount = prompt(
        `請輸入退款/沖銷金額 (可退款上限: ${maxRefundableAmount.toLocaleString('zh-TW')})`,
        maxRefundableAmount.toString()
      );

      if (amount === null) {
        return;
      }

      const parsedAmount = Number(amount);
      if (!Number.isFinite(parsedAmount) || parsedAmount <= 0) {
        this.messageService.add({ severity: 'error', summary: '輸入無效', detail: '退款金額必須是大於 0 的數字。' });
        return;
      }

      if (parsedAmount > maxRefundableAmount) {
        this.messageService.add({ severity: 'error', summary: '超出上限', detail: '退款金額不可超過可退款上限。' });
        return;
      }

      this.accountingService.refundTransaction(txn.id, parsedAmount).subscribe({
        next: () => {
          this.messageService.add({ severity: 'success', summary: '成功', detail: '已建立沖銷交易' });
          this.loadData();
        },
        error: () => this.messageService.add({ severity: 'error', summary: '錯誤', detail: '沖銷失敗' })
      });
  }

    private getMaxRefundableAmount(txn: Transaction) {
      return Math.max(0, txn.refundableAmount ?? txn.transactionAmount);
    }

  getCategoryColor(name: string) {
      const cat = this.categories().find(c => c.name === name);
      return cat ? cat.color : '#64748b'; // 使用更明亮的藍灰色作為預設
  }

  getCategoryTagStyle(name: string) {
      const hex = this.getCategoryColor(name);
      const rgb = this.hexToRgb(hex);
      if (!rgb) {
          return {
              'background-color': 'rgba(100, 116, 139, 0.16)',
              'border': '1px solid rgba(100, 116, 139, 0.38)',
              'color': '#334155'
          };
      }
      return {
          'background-color': `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.16)`,
          'border': `1px solid rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.42)`,
          'color': `rgb(${Math.max(40, Math.round(rgb.r * 0.65))}, ${Math.max(40, Math.round(rgb.g * 0.65))}, ${Math.max(40, Math.round(rgb.b * 0.65))})`
      };
  }

  private hexToRgb(hex: string) {
      const normalized = hex?.replace('#', '').trim();
      if (!normalized || !/^[0-9a-fA-F]{6}$/.test(normalized)) return null;
      const r = parseInt(normalized.substring(0, 2), 16);
      const g = parseInt(normalized.substring(2, 4), 16);
      const b = parseInt(normalized.substring(4, 6), 16);
      return { r, g, b };
  }

  saveTransaction() {
    if (this.newTxn.transactionType === 'CARD' && !this.newTxn.cardId) {
      this.messageService.add({ severity: 'warn', summary: '提醒', detail: '信用卡交易必須選擇卡片' });
      return;
    }
    if (!this.canSaveTransaction()) {
      this.messageService.add({ severity: 'warn', summary: '提醒', detail: '請填寫必要欄位' });
      return;
    }
    const year = this.txnDate.getFullYear();
    const month = (this.txnDate.getMonth() + 1).toString().padStart(2, '0');
    const day = this.txnDate.getDate().toString().padStart(2, '0');
    const dateStr = `${year}-${month}-${day}`;
    const payload = {
      date: dateStr,
      categoryId: this.newTxn.categoryId,
      item: this.newTxn.item,
      paidAmount: this.newTxn.paidAmount,
      transactionAmount: this.newTxn.transactionAmount,
      paymentMethod: this.newTxn.paymentMethod,
      cardId: this.newTxn.cardId,
      transactionType: this.newTxn.transactionType === 'CARD' ? 'EXPENSE' : this.newTxn.transactionType,
      note: this.newTxn.note
    };

    if (this.isEdit) {
        this.accountingService.updateTransaction(this.newTxn.id, payload).subscribe({
            next: () => {
              this.messageService.add({ severity: 'success', summary: '成功', detail: '交易已更新' });
              this.displayDialog = false;
              this.loadData();
            },
            error: () => this.messageService.add({ severity: 'error', summary: '錯誤', detail: '更新失敗' })
          });
    } else {
        this.accountingService.createTransaction(payload).subscribe({
            next: () => {
              this.messageService.add({ severity: 'success', summary: '成功', detail: '交易已建立' });
              this.displayDialog = false;
              this.loadData();
            },
            error: () => this.messageService.add({ severity: 'error', summary: '錯誤', detail: '建立失敗' })
          });
    }
  }

  canSaveTransaction(): boolean {
    const hasRequiredCard = this.newTxn.transactionType !== 'CARD' || !!this.newTxn.cardId;
    return !!this.newTxn.item?.trim()
      && !!this.newTxn.categoryId
      && Number(this.newTxn.transactionAmount) > 0
      && this.txnDate instanceof Date
      && !Number.isNaN(this.txnDate.getTime())
      && hasRequiredCard;
  }

  formatCurrency(value: number | string | null | undefined): string {
    return new Intl.NumberFormat('zh-TW', {
      style: 'currency',
      currency: 'TWD',
      minimumFractionDigits: 0,
    }).format(Number(value ?? 0));
  }

  deleteTransaction(id: number) {
    if (confirm('確定要刪除此筆交易嗎？')) {
      this.accountingService.deleteTransaction(id).subscribe({
        next: () => {
          this.messageService.add({ severity: 'success', summary: '成功', detail: '交易已刪除' });
          this.loadData();
        },
        error: () => this.messageService.add({ severity: 'error', summary: '錯誤', detail: '刪除失敗' })
      });
    }
  }

  generateRecurring() {
      if (this.isGeneratingRecurring) return;
      const now = new Date();
      const yearMonth = `${now.getFullYear()}年${now.getMonth() + 1}月`;
      const confirmed = confirm(`確定要同步 ${yearMonth} 的固定支出、訂閱與分期扣款嗎？`);
      if (!confirmed) return;

      this.isGeneratingRecurring = true;
      this.accountingService.triggerRecurringGeneration().subscribe({
          next: () => {
              this.messageService.add({ severity: 'success', summary: '同步完成', detail: `已同步 ${yearMonth} 定期交易` });
              this.loadData();
          },
          error: () => this.messageService.add({ severity: 'error', summary: '錯誤', detail: '同步失敗，請稍後再試' }),
          complete: () => { this.isGeneratingRecurring = false; }
      });
  }
}
