import type { NotificationItem, User } from '../types';
import { request } from './client';

export const listNotifications = (u: User) => request<NotificationItem[]>(u, 'GET', '/notifications');

export const markAllNotificationsRead = (u: User) => request<void>(u, 'POST', '/notifications/read');

export const markNotificationRead = (u: User, id: number) => request<void>(u, 'POST', `/notifications/${id}/read`);
