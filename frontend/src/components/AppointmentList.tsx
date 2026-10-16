import type { Appointment, User } from '../types';
import { AppointmentCard } from './AppointmentCard';

interface Props {
  user: User;
  appointments: Appointment[] | null;
  busyId: number | null;
  onConfirm: (a: Appointment) => void;
  onCancel: (a: Appointment) => void;
  onReschedule: (a: Appointment) => void;
}

export function AppointmentList({ user, appointments, busyId, onConfirm, onCancel, onReschedule }: Props) {
  return (
    <section>
      <h2>{user.role === 'patient' ? 'Your appointments' : 'Appointments assigned to you'}</h2>
      {!appointments && <p className="hint">Loading appointments…</p>}
      {appointments?.length === 0 && <p className="hint">No appointments yet.</p>}
      {appointments?.map((a) => (
        <AppointmentCard key={a.id} appointment={a} user={user} busy={busyId === a.id}
          onConfirm={() => onConfirm(a)} onCancel={() => onCancel(a)} onReschedule={() => onReschedule(a)} />
      ))}
    </section>
  );
}
