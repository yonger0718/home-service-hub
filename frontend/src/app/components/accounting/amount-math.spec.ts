import { describe, expect, it } from 'vitest';

import {
  KEYPAD_CALCULATOR,
  KEYPAD_PHONE,
  KeypadState,
  applyKey,
  evaluateAmount,
  keypadState,
  prettyExpression,
  roundHalfAway,
} from './amount-math';

function value(expression: string, decimals = 0): number | string {
  const result = evaluateAmount(expression, decimals);
  return result.ok ? result.value : `error: ${result.error}`;
}

function press(keys: string[], decimals = 0, start = keypadState('', decimals)): KeypadState {
  return keys.reduce((state, key) => applyKey(state, key), start);
}

describe('evaluateAmount', () => {
  it('evaluates precedence, division rounding, unary minus and trailing operators', () => {
    expect(value('3×40')).toBe(120);
    expect(value('2+3×4')).toBe(14);
    expect(value('(2+3)×4')).toBe(20);
    expect(value('10−4−3')).toBe(3);
    expect(value('100÷3', 0)).toBe(33);
    expect(value('100÷3', 2)).toBe(33.33);
    expect(value('2÷3', 0)).toBe(1);
    expect(value('−5+8')).toBe(3);
    expect(value('3×−2')).toBe(-6);
    expect(value('2−−3')).toBe(5);
    expect(value('−(2+3)')).toBe(-5);
    expect(value('12+')).toBe(12);
    expect(value('12×(')).toBe(12);
    expect(value('3×40=')).toBe(120);
    expect(value('3×40==')).toBe(120);
    expect(value('(1+2')).toBe(3);
    expect(value('-0.4', 0)).toBe(0);
    expect(Object.is(value('-0.4', 0), -0)).toBe(false);
    expect(value('1.005', 2)).toBe(1.01);
    expect(value('-2.5', 0)).toBe(-3);
    expect(value('0.1+0.2', 2)).toBe(0.3);
  });

  it('accepts full-width digits, grouping commas and ASCII operators', () => {
    expect(value('１２０')).toBe(120);
    expect(value('１，２００＋３５')).toBe(1235);
    expect(value('1,200+35')).toBe(1235);
    expect(value('12．5×2', 2)).toBe(25);
    expect(value('（１＋２）＊３')).toBe(9);
    expect(value(' 7 * 6 ')).toBe(42);
    expect(value('84/2')).toBe(42);
    expect(value('.5+5.', 2)).toBe(5.5);
  });

  it('rejects division by zero and malformed input without returning NaN', () => {
    expect(evaluateAmount('1/0', 0)).toEqual({ ok: false, error: 'division by zero' });
    expect(evaluateAmount('5÷(2−2)', 0)).toEqual({ ok: false, error: 'division by zero' });
    expect(evaluateAmount('', 0)).toEqual({ ok: false, error: 'empty' });
    expect(evaluateAmount('−', 0)).toEqual({ ok: false, error: 'empty' });
    expect(evaluateAmount('1.2.3', 0).ok).toBe(false);
    expect(evaluateAmount('5)', 0).ok).toBe(false);
    expect(evaluateAmount('2(3)', 0).ok).toBe(false);
    expect(evaluateAmount('×5', 0).ok).toBe(false);
    expect(evaluateAmount('abc', 0).ok).toBe(false);
    expect(evaluateAmount('()', 0).ok).toBe(false);
  });
});

describe('roundHalfAway', () => {
  it('rounds halves away from zero', () => {
    expect(roundHalfAway(2.5, 0)).toBe(3);
    expect(roundHalfAway(-2.5, 0)).toBe(-3);
    expect(roundHalfAway(1165.857, 0)).toBe(1166);
    expect(roundHalfAway(33.335, 2)).toBe(33.34);
  });
});

describe('keypad layouts', () => {
  it('matches the spec rows', () => {
    expect(KEYPAD_CALCULATOR).toEqual([
      ['÷', '×', '−', '+'],
      ['7', '8', '9', '⌫'],
      ['4', '5', '6', 'C'],
      ['1', '2', '3', '↵'],
      ['.', '0', '00', '✓'],
    ]);
    expect(KEYPAD_PHONE).toEqual([
      ['÷', '×', '−', '+'],
      ['1', '2', '3', '⌫'],
      ['4', '5', '6', 'C'],
      ['7', '8', '9', '↵'],
      ['.', '0', '00', '✓'],
    ]);
  });
});

describe('applyKey', () => {
  it('builds 3×40 and evaluates it on ✓', () => {
    const typed = press(['3', '×', '4', '0']);
    expect(typed.expression).toBe('3×40');
    expect(typed.display).toBe('3×40');

    const saved = applyKey(typed, '✓');
    expect(saved.expression).toBe('120');
    expect(saved.error).toBeNull();
    expect(applyKey(saved, '✓').expression).toBe('120');
  });

  it('appends 00 after a digit and ignores it on an empty or zero number', () => {
    expect(press(['1', '00']).expression).toBe('100');
    expect(press(['00']).expression).toBe('0');
    expect(press(['0', '00']).expression).toBe('0');
    expect(press(['0', '5']).expression).toBe('5');
  });

  it('allows one decimal point per number and a leading 0.', () => {
    expect(press(['.', '5']).expression).toBe('0.5');
    expect(press(['1', '.', '.', '2'], 2).expression).toBe('1.2');
    expect(press(['1', '.', '2', '+', '.', '3'], 2).expression).toBe('1.2+0.3');
  });

  it('replaces a trailing operator but keeps a unary minus after × and ÷', () => {
    expect(press(['5', '+', '×']).expression).toBe('5×');
    expect(press(['5', '×', '−', '2']).expression).toBe('5×−2');
    expect(press(['−', '5']).expression).toBe('−5');
    expect(press(['+']).expression).toBe('');
  });

  it('deletes with ⌫ and clears with C', () => {
    expect(press(['1', '2', '3', '⌫']).expression).toBe('12');
    expect(press(['1', '2', 'C']).expression).toBe('');
    expect(press(['1', '2', 'C']).display).toBe('0');
  });

  it('keeps the expression and reports the error when it cannot be evaluated', () => {
    const state = press(['1', '÷', '0', '✓']);
    expect(state.expression).toBe('1÷0');
    expect(state.error).toBe('division by zero');
  });

  it('groups thousands in the display only', () => {
    const state = press(['1', '2', '0', '0', '+', '3', '5']);
    expect(state.expression).toBe('1200+35');
    expect(state.display).toBe('1,200+35');
    expect(prettyExpression('-1234.5*2')).toBe('−1,234.5×2');
  });
});
