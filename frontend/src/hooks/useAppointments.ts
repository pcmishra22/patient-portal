import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api';
import type { Appointment, NewAppointment, User } from '../types';
import { formatStamp } from '../utils/format';

const OFFLINE = 'Could not reach the server. Is the backend running on port 8000?';

function describe(e: unknown): string {
  if (!(e instanceof api.ApiError)) return OFFLINE;
  if (e.code === 'stale' && e.current) {  // only a stale view needs the 'it is now ...' hint; overlaps are self-explanatory
    return `${e.message} It is now ${e.current.status}, ${formatStamp(e.current.startTime)}. Review the latest details and try again.`;
  }
  return e.message;
}

/** Appointment list for the acting user + every mutation. After ANY mutation (success or failure) the list is
 *  reloaded, so the UI always converges on server truth; a failure becomes a notice. */
export function useAppointments(user: User | null) {
  const [appointments, setAppointments] = useState<Appointment[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const latest = useRef(0); // ignore out-of-order responses (e.g. after quickly switching roles)

  const load = useCallback(async () => {
    if (!user) return;
    const mine = ++latest.current;
    try {
      const list = await api.listAppointments(user);
      if (mine === latest.current) setAppointments(list);
    } catch { setNotice(OFFLINE); }
  }, [user]);

  useEffect(() => { setAppointments(null); setNotice(null); void load(); }, [load]);

  const run = useCallback(async (id: number | null, fn: (u: User) => Promise<unknown>) => {
    if (!user) return;
    setBusyId(id); setNotice(null);
    try { await fn(user); } catch (e) { setNotice(describe(e)); }
    finally { await load(); setBusyId(null); }
  }, [user, load]);

  return {
    appointments, notice, busyId,
    dismissNotice: () => setNotice(null),
    create: (form: NewAppointment) => run(null, (u) => api.createAppointment(u, form)),
    confirm: (a: Appointment) => run(a.id, (u) => api.confirmAppointment(u, a)),
    cancel: (a: Appointment) => run(a.id, (u) => api.cancelAppointment(u, a)),
    reschedule: (a: Appointment, startTime: string) => run(a.id, (u) => api.rescheduleAppointment(u, a, startTime)),
  };
}
