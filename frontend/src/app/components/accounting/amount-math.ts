/**
 * One evaluator for the phone keypad and the desktop amount field (spec "Amount input").
 * Pure functions only; no Angular imports.
 */

export type AmountResult = { ok: true; value: number } | { ok: false; error: string };

export interface KeypadState {
  /** What the owner typed, with display operators (`÷ × − +`) and no grouping. */
  expression: string;
  /** `expression` with thousands separators, or `0` when empty. */
  display: string;
  /** Currency decimals used when the expression is evaluated. */
  decimals: number;
  /** Evaluation error of the last ↵ / ✓, otherwise null. */
  error: string | null;
}

export const KEYPAD_CALCULATOR: readonly (readonly string[])[] = [
  ['÷', '×', '−', '+'],
  ['7', '8', '9', '⌫'],
  ['4', '5', '6', 'C'],
  ['1', '2', '3', '↵'],
  ['.', '0', '00', '✓'],
];

export const KEYPAD_PHONE: readonly (readonly string[])[] = [
  ['÷', '×', '−', '+'],
  ['1', '2', '3', '⌫'],
  ['4', '5', '6', 'C'],
  ['7', '8', '9', '↵'],
  ['.', '0', '00', '✓'],
];

const SYMBOL_MAP: Record<string, string> = {
  '．': '.',
  '，': ',',
  '＋': '+',
  '－': '-',
  '−': '-',
  '×': '*',
  '＊': '*',
  '÷': '/',
  '／': '/',
  '（': '(',
  '）': ')',
  '＝': '=',
  '　': ' ',
};

const MAX_FRACTION_DIGITS = 4;

type Operator = '+' | '-' | '*' | '/' | 'neg';
type Token =
  | { type: 'num'; value: number }
  | { type: 'op'; op: Operator }
  | { type: 'lparen' }
  | { type: 'rparen' };

const PRECEDENCE: Record<Operator, number> = { '+': 1, '-': 1, '*': 2, '/': 2, neg: 3 };

/** Full-width → ASCII, display operators → ASCII, grouping commas and spaces removed. */
function normalise(expression: string): string {
  let out = '';
  for (const ch of expression) {
    const code = ch.charCodeAt(0);
    if (code >= 0xff10 && code <= 0xff19) {
      out += String.fromCharCode(code - 0xfee0);
    } else {
      out += SYMBOL_MAP[ch] ?? ch;
    }
  }
  return out.replace(/[\s,]/g, '');
}

function tokenise(text: string): Token[] | string {
  const tokens: Token[] = [];
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (/[0-9.]/.test(ch)) {
      let j = i;
      while (j < text.length && /[0-9.]/.test(text[j])) {
        j++;
      }
      const raw = text.slice(i, j);
      if (raw === '.' || raw.split('.').length > 2) {
        return `invalid number "${raw}"`;
      }
      tokens.push({ type: 'num', value: Number(raw) });
      i = j;
      continue;
    }
    if (ch === '+' || ch === '-' || ch === '*' || ch === '/') {
      const prev = tokens.at(-1);
      const unary = !prev || prev.type === 'op' || prev.type === 'lparen';
      if (unary) {
        if (ch === '-') {
          tokens.push({ type: 'op', op: 'neg' });
        } else if (ch !== '+') {
          return `unexpected "${ch}"`;
        }
      } else {
        tokens.push({ type: 'op', op: ch });
      }
      i++;
      continue;
    }
    if (ch === '(') {
      tokens.push({ type: 'lparen' });
      i++;
      continue;
    }
    if (ch === ')') {
      tokens.push({ type: 'rparen' });
      i++;
      continue;
    }
    return `unexpected "${ch}"`;
  }
  return tokens;
}

/** Shunting-yard: infix tokens → reverse Polish. `neg` is a prefix operator, so it never pops. */
function toRpn(tokens: Token[]): Token[] | string {
  const output: Token[] = [];
  const stack: Token[] = [];
  for (const token of tokens) {
    if (token.type === 'num') {
      output.push(token);
    } else if (token.type === 'op') {
      if (token.op !== 'neg') {
        while (stack.length) {
          const top = stack.at(-1)!;
          if (top.type !== 'op' || PRECEDENCE[top.op] < PRECEDENCE[token.op]) {
            break;
          }
          output.push(stack.pop()!);
        }
      }
      stack.push(token);
    } else if (token.type === 'lparen') {
      stack.push(token);
    } else {
      while (stack.length && stack.at(-1)!.type !== 'lparen') {
        output.push(stack.pop()!);
      }
      if (!stack.length) {
        return 'unbalanced parentheses';
      }
      stack.pop();
    }
  }
  while (stack.length) {
    const top = stack.pop()!;
    if (top.type !== 'lparen') {
      output.push(top); // an unclosed "(" is closed at the end
    }
  }
  return output;
}

/** Round half away from zero; never returns -0. Uses toPrecision to absorb binary noise (1.005 → 1.01). */
export function roundHalfAway(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  const scaled = Number((Math.abs(value) * factor).toPrecision(15));
  const rounded = Math.round(scaled) / factor;
  return value < 0 && rounded !== 0 ? -rounded : rounded;
}

export function evaluateAmount(expression: string, decimals: number): AmountResult {
  const text = normalise(expression).replace(/=+$/, '');
  if (text === '') {
    return { ok: false, error: 'empty' };
  }
  const tokens = tokenise(text);
  if (typeof tokens === 'string') {
    return { ok: false, error: tokens };
  }
  while (tokens.length) {
    const last = tokens.at(-1)!;
    if (last.type !== 'op' && last.type !== 'lparen') {
      break;
    }
    tokens.pop(); // a trailing operator or "(" is ignored, as on a calculator
  }
  if (!tokens.length) {
    return { ok: false, error: 'empty' };
  }
  const rpn = toRpn(tokens);
  if (typeof rpn === 'string') {
    return { ok: false, error: rpn };
  }
  const values: number[] = [];
  for (const token of rpn) {
    if (token.type === 'num') {
      values.push(token.value);
      continue;
    }
    if (token.type !== 'op') {
      return { ok: false, error: 'invalid expression' };
    }
    if (token.op === 'neg') {
      if (!values.length) {
        return { ok: false, error: 'invalid expression' };
      }
      values.push(-values.pop()!);
      continue;
    }
    if (values.length < 2) {
      return { ok: false, error: 'invalid expression' };
    }
    const right = values.pop()!;
    const left = values.pop()!;
    if (token.op === '/' && right === 0) {
      return { ok: false, error: 'division by zero' };
    }
    values.push(
      token.op === '+' ? left + right : token.op === '-' ? left - right : token.op === '*' ? left * right : left / right,
    );
  }
  if (values.length !== 1 || !Number.isFinite(values[0])) {
    return { ok: false, error: 'invalid expression' };
  }
  return { ok: true, value: roundHalfAway(values[0], decimals) };
}

/** Display form: ASCII operators as `÷ × − +`, thousands separators inside each number. */
export function prettyExpression(expression: string): string {
  return expression
    .replace(/-/g, '−')
    .replace(/\*/g, '×')
    .replace(/\//g, '÷')
    .replace(/\d+(\.\d*)?/g, match => {
      const [whole, fraction] = match.split('.');
      const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
      return fraction === undefined ? grouped : `${grouped}.${fraction}`;
    });
}

export function keypadState(expression = '', decimals = 0): KeypadState {
  return { expression, display: prettyExpression(expression) || '0', decimals, error: null };
}

const OPERATOR_KEYS = ['÷', '×', '−', '+'];

function currentNumber(expression: string): string {
  return /[0-9.]*$/.exec(expression)?.[0] ?? '';
}

function appendDigit(expression: string, digit: string): string {
  const number = currentNumber(expression);
  if (number === '0') {
    return expression.slice(0, -1) + digit;
  }
  const fraction = number.includes('.') ? number.split('.')[1] : '';
  if (fraction.length >= MAX_FRACTION_DIGITS) {
    return expression;
  }
  return expression + digit;
}

function appendDoubleZero(expression: string): string {
  const number = currentNumber(expression);
  if (number === '') {
    return expression + '0';
  }
  if (number === '0') {
    return expression;
  }
  return appendDigit(appendDigit(expression, '0'), '0');
}

function appendDot(expression: string): string {
  const number = currentNumber(expression);
  if (number.includes('.')) {
    return expression;
  }
  return expression + (number === '' ? '0.' : '.');
}

function appendOperator(expression: string, operator: string): string {
  if (expression === '') {
    return operator === '−' ? '−' : '';
  }
  const last = expression.at(-1)!;
  if (OPERATOR_KEYS.includes(last)) {
    if (operator === '−' && (last === '×' || last === '÷')) {
      return expression + operator;
    }
    const trimmed = expression.replace(/[÷×−+]+$/, '');
    return trimmed === '' ? (operator === '−' ? '−' : '') : trimmed + operator;
  }
  return expression + operator;
}

/** Plain result text for the keypad: no grouping, `−` for negatives, no trailing zeros. */
function resultText(value: number, decimals: number): string {
  const text = String(Number(value.toFixed(decimals)));
  return text.startsWith('-') ? `−${text.slice(1)}` : text;
}

/** Keypad state machine. `↵` and `✓` evaluate; the component decides what happens next. */
export function applyKey(state: KeypadState, key: string): KeypadState {
  const next = (expression: string, error: string | null = null): KeypadState => ({
    ...state,
    expression,
    display: prettyExpression(expression) || '0',
    error,
  });
  const expression = state.expression;
  if (/^[0-9]$/.test(key)) {
    return next(appendDigit(expression, key));
  }
  switch (key) {
    case '00':
      return next(appendDoubleZero(expression));
    case '.':
      return next(appendDot(expression));
    case '÷':
    case '×':
    case '−':
    case '+':
      return next(appendOperator(expression, key));
    case '⌫':
      return next(expression.slice(0, -1));
    case 'C':
      return next('');
    case '↵':
    case '✓':
    case '=': {
      if (expression === '') {
        return next('');
      }
      const result = evaluateAmount(expression, state.decimals);
      return result.ok ? next(resultText(result.value, state.decimals)) : next(expression, result.error);
    }
    default:
      return state;
  }
}
