import { Component } from '@angular/core';

import { ShellComponent } from './components/shell/shell';

@Component({
  selector: 'app-root',
  imports: [ShellComponent],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App {}
