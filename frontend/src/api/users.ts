import type { User } from '../types';
import { request } from './client';

export const listUsers = () => request<User[]>(null, 'GET', '/users');
