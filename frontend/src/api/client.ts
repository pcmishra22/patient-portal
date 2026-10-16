import type { Appointment, User } from '../types';

/** Thrown for any non-2xx response. On 409 (stale version / overlap) `current` is the latest server copy. */
export class ApiError extends Error {
  constructor(public status: number, message: string, public current?: Appointment, public code?: string) { super(message); }
}

export async function request<T>(user: User | null, method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (user) headers['X-User-Id'] = user.id; // demo identity; real auth is out of scope

  const res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.ok) return (res.status === 204 ? undefined : await res.json()) as T; // 204 = no body

  let data: { message?: string; code?: string; detail?: unknown; current?: Appointment } = {};
  try { data = await res.json(); } catch { /* non-JSON error body */ }
  const message = data.message
    ?? (Array.isArray(data.detail) ? 'Please check the values you entered.' : 'Something went wrong. Please try again.');
  throw new ApiError(res.status, message, data.current, data.code);
}
