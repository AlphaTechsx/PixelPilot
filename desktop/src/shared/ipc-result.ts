export type IpcResult<T> =
  | {
      ok: true;
      value: T;
    }
  | {
      ok: false;
      error: string;
    };

export function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    return error.message || fallback;
  }
  if (typeof error === 'string' && error.trim()) {
    return error;
  }
  return fallback;
}

export function successResult<T>(value: T): IpcResult<T> {
  return {
    ok: true,
    value
  };
}

export function failureResult<T = never>(error: unknown, fallback: string = 'PixelPilot action failed.'): IpcResult<T> {
  return {
    ok: false,
    error: errorMessage(error, fallback)
  };
}

export function unwrapIpcResult<T>(result: IpcResult<T>): T {
  if (!result.ok) {
    throw new Error(result.error);
  }
  return result.value;
}
