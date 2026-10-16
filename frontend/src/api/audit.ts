import type { Action, AuditEntry, User } from '../types';
import { request } from './client';

export const getAuditLog = (u: User, action?: Action) =>
  request<AuditEntry[]>(u, 'GET', `/audit-log${action ? `?action=${action}` : ''}`);
