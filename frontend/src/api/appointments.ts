import type { Appointment, AppointmentEvent, NewAppointment, User } from '../types';
import { request } from './client';

export const listAppointments = (u: User) => request<Appointment[]>(u, 'GET', '/appointments');

export const createAppointment = (u: User, form: NewAppointment) =>
  request<Appointment>(u, 'POST', '/appointments', form);

export const confirmAppointment = (u: User, a: Appointment) =>
  request<Appointment>(u, 'POST', `/appointments/${a.id}/confirm`, { version: a.version });

export const rescheduleAppointment = (u: User, a: Appointment, startTime: string) =>
  request<Appointment>(u, 'PATCH', `/appointments/${a.id}/reschedule`, { version: a.version, startTime });

export const cancelAppointment = (u: User, a: Appointment) =>
  request<Appointment>(u, 'POST', `/appointments/${a.id}/cancel`, { version: a.version });

export const getHistory = (u: User, appointmentId: number) =>
  request<AppointmentEvent[]>(u, 'GET', `/appointments/${appointmentId}/history`);
