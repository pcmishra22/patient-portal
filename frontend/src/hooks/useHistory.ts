import { useEffect, useState } from 'react';
import { getHistory } from '../api';
import type { AppointmentEvent, User } from '../types';

/** Audit trail for one appointment; refetches when the appointment's version changes. */
export function useHistory(user: User, appointmentId: number, version: number) {
  const [events, setEvents] = useState<AppointmentEvent[] | null>(null);
  useEffect(() => {
    setEvents(null);
    getHistory(user, appointmentId).then(setEvents).catch(() => setEvents([]));
  }, [user, appointmentId, version]);
  return events;
}
