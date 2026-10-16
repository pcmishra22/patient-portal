export type Role = 'patient' | 'provider';
export type Status = 'pending' | 'confirmed' | 'cancelled';
export type Action = 'created' | 'confirmed' | 'rescheduled' | 'cancelled';

export interface User { id: string; name: string; role: Role }

export interface Appointment {
  id: number;
  patientId: string; patientName: string;
  providerId: string; providerName: string;
  startTime: string; endTime: string; // ISO 8601, UTC
  appointmentType: string; reason: string;
  status: Status;
  version: number; // echoed back on every mutation (optimistic concurrency)
}

export interface AppointmentEvent {
  id: number; appointmentId: number;
  actorRole: Role; actorName: string;
  action: Action;
  fromStatus: Status | null; toStatus: Status;
  fromStart: string | null; toStart: string;
  createdAt: string;
}

export interface NewAppointment {
  providerId: string; startTime: string; durationMin: number; appointmentType: string; reason: string;
}

/** One row of the global audit log: an event plus the appointment context needed to read it standalone. */
export interface AuditEntry extends AppointmentEvent {
  appointmentType: string; patientName: string; providerName: string;
}

export type DeliveryStatus = 'pending' | 'sending' | 'sent' | 'failed';

/** In-app inbox item (the same row the email/SMS stub is sent from). readAt === null means unread. */
export interface NotificationItem {
  id: number; appointmentId: number;
  kind: Action; channel: string;
  subject: string; body: string;
  deliveryStatus: DeliveryStatus;
  gaveUp: boolean; // every send attempt failed; nothing more will be sent
  createdAt: string; readAt: string | null;
}
