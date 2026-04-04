import { describe, expect, it } from 'vitest';
import { errorMessage, failureResult, successResult, unwrapIpcResult } from './ipc-result.js';

describe('ipc-result helpers', () => {
  it('unwraps successful results', () => {
    expect(unwrapIpcResult(successResult({ ready: true }))).toEqual({ ready: true });
  });

  it('throws the ipc error message for failed results', () => {
    expect(() => unwrapIpcResult(failureResult(new Error('Bridge offline')))).toThrow('Bridge offline');
  });

  it('normalizes non-error failures into a readable message', () => {
    expect(errorMessage('', 'Fallback message')).toBe('Fallback message');
    expect(failureResult('Custom failure')).toEqual({
      ok: false,
      error: 'Custom failure'
    });
  });
});
