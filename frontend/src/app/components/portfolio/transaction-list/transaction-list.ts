import { Component, OnInit, inject, signal, ViewChild, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PortfolioService } from '../../../services/portfolio.service';
import { Transaction, TransactionType } from '../../../models/portfolio.model';
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { InputNumberModule } from 'primeng/inputnumber';
import { SelectButtonModule } from 'primeng/selectbutton';
import { DatePickerModule } from 'primeng/datepicker';
import { ConfirmationService, MessageService, MenuItem } from 'primeng/api';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { ToastModule } from 'primeng/toast';
import { MenuModule } from 'primeng/menu';
import { Menu } from 'primeng/menu';
import { ListItemComponent } from '../../shared/list-item/list-item';

@Component({
  selector: 'app-portfolio-transactions',
  imports: [CommonModule, TableModule, ButtonModule, DialogModule, FormsModule, InputTextModule, InputNumberModule, SelectButtonModule, DatePickerModule, ConfirmDialogModule, ToastModule, MenuModule, ListItemComponent],
  providers: [ConfirmationService, MessageService],
  templateUrl: './transaction-list.html',
  styleUrl: './transaction-list.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class PortfolioTransactionListComponent implements OnInit {
  private portfolioService = inject(PortfolioService);
  private confirmationService = inject(ConfirmationService);
  private messageService = inject(MessageService);

  @ViewChild('menu') menu!: Menu;
  menuItems: MenuItem[] = [];

  transactions = signal<Transaction[]>([]);
  showDialog = signal<boolean>(false);
  isEdit = signal<boolean>(false);
  
  newTransaction: Partial<Transaction> = {
    type: TransactionType.BUY,
    quantity: 0,
    price: 0,
    fee: 0,
    tax: 0
  };

  transactionTypes = [
    { label: '買進', value: TransactionType.BUY },
    { label: '賣出', value: TransactionType.SELL }
  ];

  ngOnInit() {
    this.loadTransactions();
  }

  loadTransactions() {
    this.portfolioService.getTransactions().subscribe(data => {
      this.transactions.set(data);
    });
  }

  showMenu(event: MouseEvent, transaction: Transaction) {
    this.menuItems = [
      { label: '編輯', icon: 'pi pi-pencil', command: () => this.editTransaction(transaction) },
      { separator: true },
      { label: '刪除', icon: 'pi pi-trash', styleClass: 'text-danger', command: () => this.deleteTransaction(transaction) }
    ];
    this.menu.toggle(event);
  }

  openNew() {
    this.isEdit.set(false);
    this.newTransaction = { type: TransactionType.BUY, quantity: 0, price: 0, fee: 0, tax: 0 };
    this.showDialog.set(true);
  }

  editTransaction(transaction: Transaction) {
    this.isEdit.set(true);
    this.newTransaction = { ...transaction, trade_date: transaction.trade_date ? new Date(transaction.trade_date) : undefined };
    this.showDialog.set(true);
  }

  deleteTransaction(transaction: Transaction) {
    this.confirmationService.confirm({
      message: `確定要刪除 ${transaction.symbol} 的這筆交易紀錄嗎？`,
      header: '確認刪除',
      icon: 'pi pi-exclamation-triangle',
      accept: () => {
        this.portfolioService.deleteTransaction(transaction.id).subscribe(() => {
          this.messageService.add({ severity: 'success', summary: '成功', detail: '紀錄已刪除' });
          this.loadTransactions();
        });
      }
    });
  }

  allInTotal(t: Transaction): number {
    const gross = Number(t.price) * Number(t.quantity);
    const fee = Number(t.fee || 0);
    const tax = Number(t.tax || 0);
    return t.type === TransactionType.BUY ? gross + fee + tax : gross - fee - tax;
  }

  allInUnitPrice(t: Transaction): number {
    const qty = Number(t.quantity);
    return qty > 0 ? this.allInTotal(t) / qty : Number(t.price);
  }

  saveTransaction() {
    if (this.isEdit() && this.newTransaction.id) {
      this.portfolioService.updateTransaction(this.newTransaction.id, this.newTransaction).subscribe(() => {
        this.showDialog.set(false);
        this.loadTransactions();
        this.messageService.add({ severity: 'success', summary: '成功', detail: '紀錄已更新' });
      });
    } else {
      this.portfolioService.createTransaction(this.newTransaction).subscribe(() => {
        this.showDialog.set(false);
        this.loadTransactions();
        this.messageService.add({ severity: 'success', summary: '成功', detail: '紀錄已新增' });
      });
    }
  }
}
