import { Component, OnInit, inject, signal, ViewChild, computed, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AccountingService } from '../../../services/accounting.service';
import { Subscription, Installment, Category, CreditCard, PaymentMethod } from '../../../models/accounting.model';
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { InputNumberModule } from 'primeng/inputnumber';
import { SelectModule } from 'primeng/select';
import { DatePickerModule } from 'primeng/datepicker';
import { ToastModule } from 'primeng/toast';
import { MenuModule } from 'primeng/menu';
import { MessageService, MenuItem } from 'primeng/api';
import { forkJoin } from 'rxjs';
import { Menu } from 'primeng/menu';
import { ListItemComponent } from '../../shared/list-item/list-item';

type RecurringType = 'FIXED_EXPENSE' | 'SUBSCRIPTION' | 'INSTALLMENT';

@Component({
  selector: 'app-recurring-list',
  imports: [
    CommonModule, 
    FormsModule, 
    TableModule, 
    ButtonModule, 
    TagModule,
    DialogModule,
    InputTextModule,
    InputNumberModule,
    SelectModule,
    DatePickerModule,
    ToastModule,
    MenuModule,
    ListItemComponent
  ],
  providers: [MessageService],
  templateUrl: './recurring-list.html',
  styleUrl: './recurring-list.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class RecurringListComponent implements OnInit {
  private accountingService = inject(AccountingService);
  private messageService = inject(MessageService);

  @ViewChild('menu') menu!: Menu;
  menuItems: MenuItem[] = [];

  subscriptions = signal<Subscription[]>([]);
  fixedExpenses = computed(() => this.subscriptions().filter(s => s.subType === 'FIXED_EXPENSE'));
  digitalSubscriptions = computed(() => this.subscriptions().filter(s => s.subType === 'SUBSCRIPTION'));
  
  installments = signal<Installment[]>([]);
  activeType = signal<RecurringType>('FIXED_EXPENSE');
  recurringTypeOptions: { label: string; value: RecurringType }[] = [
    { label: '固定支出', value: 'FIXED_EXPENSE' },
    { label: '數位訂閱', value: 'SUBSCRIPTION' },
    { label: '分期計畫', value: 'INSTALLMENT' }
  ];
  tableRows = computed<(Subscription | Installment)[]>(() => {
    if (this.activeType() === 'INSTALLMENT') return this.installments();
    if (this.activeType() === 'SUBSCRIPTION') return this.digitalSubscriptions();
    return this.fixedExpenses();
  });
  categories = signal<Category[]>([]);
  cards = signal<CreditCard[]>([]);
  paymentMethods = signal<PaymentMethod[]>([]);
  paymentOptions = signal<any[]>([]);

  toolOptions = computed(() => 
    this.paymentMethods().map(m => ({ label: m.name, value: m.name }))
  );

  displaySubDialog = false;
  displayInstDialog = false;
  isEditSub = false;
  isEditInst = false;

  newSub: any = this.resetSub();
  newInst: any = this.resetInst();
  selectedSubPaymentValue: string | null = null;
  selectedInstPaymentValue: string | null = null;
  instStartDate = new Date();

  ngOnInit() {
    this.loadData();
  }

  loadData() {
    forkJoin({
        subs: this.accountingService.getSubscriptions(),
        insts: this.accountingService.getInstallments(),
        cats: this.accountingService.getCategories(),
        cards: this.accountingService.getCards(),
        methods: this.accountingService.getPaymentMethods()
    }).subscribe(({ subs, insts, cats, cards, methods }) => {
        this.subscriptions.set(subs);
        this.installments.set(insts);
        this.categories.set(cats);
        this.cards.set(cards);
        this.paymentMethods.set(methods);
        
        // 建立「整合型支付選單」: 現金 + 所有的信用卡
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
    });
  }

  resetSub(type: 'FIXED_EXPENSE' | 'SUBSCRIPTION' = 'SUBSCRIPTION') {
      return { 
          name: '', 
          amount: 0, 
          categoryId: null, 
          subType: type, 
          dayOfMonth: 1, 
          paymentMethod: '信用卡',
          cardId: null
      };
  }

  setActiveType(type: RecurringType) {
      this.activeType.set(type);
  }

  isActiveType(type: RecurringType) {
      return this.activeType() === type;
  }

  isInstallmentView() {
      return this.activeType() === 'INSTALLMENT';
  }

  getCurrentTypeLabel() {
      if (this.activeType() === 'INSTALLMENT') return '分期計畫';
      if (this.activeType() === 'SUBSCRIPTION') return '數位訂閱';
      return '固定支出';
  }

  getCurrentEmptyMessage() {
      if (this.activeType() === 'INSTALLMENT') return '目前沒有分期計畫';
      if (this.activeType() === 'SUBSCRIPTION') return '目前沒有數位訂閱項目';
      return '目前沒有固定支出項目';
  }

  handleAddByType() {
      const active = this.activeType();
      if (active === 'INSTALLMENT') {
          this.showInstDialog();
          return;
      }
      this.showSubDialog(active);
  }

  showRowMenu(event: MouseEvent, row: Subscription | Installment) {
      if (this.isInstallmentView()) {
          this.showInstMenu(event, row as Installment);
      } else {
          this.showSubMenu(event, row as Subscription);
      }
  }

  showSubMenu(event: MouseEvent, sub: Subscription) {
      this.menuItems = [
          { label: '編輯', icon: 'pi pi-pencil', command: () => this.editSub(sub) },
          { separator: true },
          { label: '刪除', icon: 'pi pi-trash', styleClass: 'text-danger', command: () => this.deleteSub(sub.id) }
      ];
      this.menu.toggle(event);
  }

  showInstMenu(event: MouseEvent, inst: Installment) {
      const canDelete = this.canDeleteInstallment(inst);
      this.menuItems = [
          { label: '編輯', icon: 'pi pi-pencil', command: () => this.editInst(inst) },
          { separator: true },
          {
              label: canDelete ? '刪除' : '刪除（完成後可用）',
              icon: 'pi pi-trash',
              styleClass: 'text-danger',
              disabled: !canDelete,
              command: () => this.deleteInst(inst.id)
          }
      ];
      this.menu.toggle(event);
  }

  canDeleteInstallment(inst: Installment) {
      return inst.remainingPeriods === 0;
  }

  resetInst() {
      const now = new Date();
      const dateStr = `${now.getFullYear()}-${(now.getMonth() + 1).toString().padStart(2, '0')}-${now.getDate().toString().padStart(2, '0')}`;
      return {
          name: '',
          totalAmount: 0,
          monthlyAmount: 0,
          totalPeriods: 12,
          remainingPeriods: 12,
          startDate: dateStr,
          cardId: null,
          paymentMethod: '信用卡'
      };
  }

  showSubDialog(type: 'FIXED_EXPENSE' | 'SUBSCRIPTION' = 'SUBSCRIPTION') {
      this.newSub = this.resetSub(type);
      this.selectedSubPaymentValue = 'CASH';
      this.isEditSub = false;
      this.displaySubDialog = true;
  }

  editSub(sub: Subscription) {
      this.isEditSub = true;
      this.newSub = {
          id: sub.id,
          name: sub.name,
          amount: sub.amount,
          categoryId: sub.categoryId,
          subType: sub.subType,
          paymentMethod: sub.paymentMethod || '信用卡',
          dayOfMonth: sub.dayOfMonth,
          cardId: sub.cardId ?? null,
          active: sub.active,
      };
      this.selectedSubPaymentValue = sub.cardId ? `CARD_${sub.cardId}` : 'CASH';
      this.displaySubDialog = true;
  }

  onSubPaymentChange(event: any) {
      const selected = this.paymentOptions().find(o => o.value === event.value);
      if (!selected) return;

      if (selected.type === 'CASH') {
          this.newSub.paymentMethod = '現金';
          this.newSub.cardId = null;
      } else if (selected.type === 'CARD') {
          this.newSub.cardId = selected.cardId;
          this.newSub.paymentMethod = selected.defaultTool;
      }
  }

  onSubCategoryChange(id: number) {
      this.newSub.categoryId = id;
  }

  getCategoryColor(name: string) {
      const cat = this.categories().find(c => c.name === name);
      return cat ? cat.color : '#64748b';
  }

  showInstDialog() {
      this.newInst = this.resetInst();
      this.selectedInstPaymentValue = 'CASH';
      this.instStartDate = new Date();
      this.isEditInst = false;
      this.displayInstDialog = true;
  }

  editInst(inst: Installment) {
      this.isEditInst = true;
      this.newInst = { ...inst };
      this.selectedInstPaymentValue = inst.cardId ? `CARD_${inst.cardId}` : 'CASH';
      this.instStartDate = new Date(inst.startDate);
      this.displayInstDialog = true;
  }

  onInstPaymentChange(event: any) {
      const selected = this.paymentOptions().find(o => o.value === event.value);
      if (!selected) return;

      if (selected.type === 'CASH') {
          this.newInst.paymentMethod = '現金';
          this.newInst.cardId = null;
      } else if (selected.type === 'CARD') {
          this.newInst.cardId = selected.cardId;
          this.newInst.paymentMethod = selected.defaultTool;
      }
  }

  calcMonthly() {
      if (this.newInst.totalAmount > 0 && this.newInst.totalPeriods > 0) {
          this.newInst.monthlyAmount = Math.round(this.newInst.totalAmount / this.newInst.totalPeriods);
          // 預設剩餘期數等於總期數，方便新計畫輸入；舊計畫則可手動再修改 remainingPeriods
          if (this.newInst.remainingPeriods === 0 || this.newInst.remainingPeriods > this.newInst.totalPeriods) {
              this.newInst.remainingPeriods = this.newInst.totalPeriods;
          }
      }
  }

  saveSub() {
      const payload = {
          name: this.newSub.name,
          amount: this.newSub.amount,
          categoryId: this.newSub.categoryId,
          subType: this.newSub.subType,
          paymentMethod: this.newSub.paymentMethod,
          dayOfMonth: this.newSub.dayOfMonth,
          cardId: this.newSub.cardId,
          active: this.newSub.active,
      };

      if (this.isEditSub) {
          this.accountingService.updateSubscription(this.newSub.id, payload).subscribe({
              next: () => {
                  this.messageService.add({ severity: 'success', summary: '成功', detail: '項目已更新' });
                  this.displaySubDialog = false;
                  this.loadData();
              }
          });
      } else {
          this.accountingService.createSubscription(payload).subscribe({
              next: () => {
                  this.messageService.add({ severity: 'success', summary: '成功', detail: '項目已建立' });
                  this.displaySubDialog = false;
                  this.loadData();
              }
          });
      }
  }

  saveInst() {
      const year = this.instStartDate.getFullYear();
      const month = (this.instStartDate.getMonth() + 1).toString().padStart(2, '0');
      const day = this.instStartDate.getDate().toString().padStart(2, '0');
      const dateStr = `${year}-${month}-${day}`;
      
      this.newInst.startDate = dateStr;
      
      if (this.isEditInst) {
          this.accountingService.updateInstallment(this.newInst.id, this.newInst).subscribe({
              next: () => {
                  this.messageService.add({ severity: 'success', summary: '成功', detail: '分期計畫已更新' });
                  this.displayInstDialog = false;
                  this.loadData();
              }
          });
      } else {
          this.accountingService.createInstallment(this.newInst).subscribe({
              next: () => {
                  this.messageService.add({ severity: 'success', summary: '成功', detail: '分期計畫已建立' });
                  this.displayInstDialog = false;
                  this.loadData();
              }
          });
      }
  }

  toggleSub(id: number) {
      this.accountingService.toggleSubscription(id).subscribe(() => this.loadData());
  }

  deleteSub(id: number) {
      if (confirm('確定要刪除此訂閱項目嗎？')) {
          this.accountingService.deleteSubscription(id).subscribe(() => {
              this.messageService.add({ severity: 'success', summary: '成功', detail: '訂閱項目已刪除' });
              this.loadData();
          });
      }
  }

  deleteInst(id: number) {
      const inst = this.installments().find(item => item.id === id);
      if (inst && !this.canDeleteInstallment(inst)) {
          this.messageService.add({ severity: 'warn', summary: '尚未完成', detail: '分期計畫需在剩餘期數歸 0 後才能刪除。' });
          return;
      }

      if (confirm('確定要刪除此分期計畫嗎？')) {
          this.accountingService.deleteInstallment(id).subscribe(() => {
              this.messageService.add({ severity: 'success', summary: '成功', detail: '分期計畫已刪除' });
              this.loadData();
          });
      }
  }
}
